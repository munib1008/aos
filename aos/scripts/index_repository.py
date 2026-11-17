#!/usr/bin/env python3
"""Deterministic repository index so models read targeted files, not the whole tree.

  index_repository.py index            # build/refresh .aos/index.json (incremental by git blob hash)
  index_repository.py query "login token refresh" [--limit 8]
  index_repository.py refs SymbolName  # exact word references via git grep / rg
  index_repository.py info             # stack, tests, CI, AI-instruction files (no source)

No model is called. Unchanged files are never re-parsed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from aos_lib import atomic_write, find_root, run, state_dir, tokenize

INDEX_VERSION = 2
MAX_BYTES = 400_000
SKIP_DIRS = {".git", "node_modules", "build", "dist", ".gradle", ".aos", "__pycache__", ".venv", "venv", "target", ".idea"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".jar", ".aar", ".zip", ".so", ".pdf", ".apk", ".keystore", ".ttf", ".woff", ".woff2", ".lock"}

SYMBOL_RULES = {
    ".py": [("class", r"^\s*class\s+(\w+)"), ("function", r"^\s*(?:async\s+)?def\s+(\w+)")],
    ".kt": [("class", r"^\s*(?:(?:data|sealed|abstract|open|enum|internal|private)\s+)*(?:class|object|interface)\s+(\w+)"), ("function", r"^\s*(?:(?:private|internal|public|override|suspend|inline)\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?(\w+)")],
    ".java": [("class", r"^\s*(?:public\s+|abstract\s+|final\s+)*(?:class|interface|enum|record)\s+(\w+)"), ("function", r"^\s*(?:public|protected|private)\s+[\w<>\[\], ?]+\s+(\w+)\s*\(")],
    ".ts": [("class", r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|type|enum)\s+(\w+)"), ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"), ("function", r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::\s*[^=]+)?=>")],
    ".go": [("class", r"^type\s+(\w+)\s+(?:struct|interface)"), ("function", r"^func\s+(?:\([^)]*\)\s*)?(\w+)")],
    ".rs": [("class", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"), ("function", r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")],
    ".sql": [("table", r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:table|view|function|policy|index)\s+(?:if\s+not\s+exists\s+)?([\w.\"]+)")],
    ".sh": [("function", r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{?")],
}
for _e in (".tsx", ".js", ".jsx", ".mjs", ".cjs"):
    SYMBOL_RULES[_e] = SYMBOL_RULES[".ts"]
SYMBOL_RULES[".kts"] = SYMBOL_RULES[".kt"]

MANIFESTS = {
    "package.json": "node", "pnpm-lock.yaml": "pnpm", "tsconfig.json": "typescript", "pyproject.toml": "python",
    "requirements.txt": "python", "build.gradle.kts": "gradle/kotlin", "build.gradle": "gradle", "settings.gradle.kts": "gradle",
    "pom.xml": "maven", "go.mod": "go", "Cargo.toml": "rust", "Dockerfile": "docker", "docker-compose.yml": "docker-compose",
    "next.config.js": "nextjs", "next.config.mjs": "nextjs", "vite.config.ts": "vite", "AndroidManifest.xml": "android",
    "supabase/config.toml": "supabase", "vercel.json": "vercel",
}
AI_FILES = ["CLAUDE.md", "AGENTS.md", ".cursorrules", ".github/copilot-instructions.md", "GEMINI.md"]
TEST_RX = re.compile(r"(^|/)(tests?|__tests__|spec|androidTest|e2e)/|(_test|\.test|\.spec|Test|Tests|IT)\.[A-Za-z]+$|(^|/)test_[^/]+\.py$")


def list_files(root: Path) -> dict[str, str]:
    """path -> content hash id (git blob hash if available, else size:mtime)."""
    rc, out = run(["git", "ls-files", "-s", "-z"], cwd=root, timeout=120)
    files: dict[str, str] = {}
    if rc == 0 and out:
        for entry in out.split("\0"):
            if "\t" in entry:
                meta, path = entry.split("\t", 1)
                files[path] = meta.split()[1]
    else:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                p = Path(dp) / fn
                try:
                    st = p.stat()
                except OSError:
                    continue
                files[str(p.relative_to(root))] = f"{st.st_size}:{st.st_mtime_ns}"
    return {p: h for p, h in files.items() if not (set(Path(p).parts) & SKIP_DIRS)}


def extract(root: Path, rel: str) -> dict:
    p = root / rel
    ext = p.suffix.lower()
    entry = {"ext": ext, "symbols": [], "lines": 0}
    if ext in BINARY_EXT or not p.is_file():
        entry["skipped"] = "binary-or-missing"
        return entry
    try:
        if p.stat().st_size > MAX_BYTES:
            entry["skipped"] = "too-large"
            return entry
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        entry["skipped"] = "unreadable"
        return entry
    lines = text.splitlines()
    entry["lines"] = len(lines)
    rules = [(k, re.compile(rx)) for k, rx in SYMBOL_RULES.get(ext, [])]
    for n, line in enumerate(lines, 1):
        for kind, rx in rules:
            m = rx.match(line)
            if m:
                entry["symbols"].append([m.group(1), kind, n])
                break
    return entry


def build(root: Path, force: bool = False) -> dict:
    path = state_dir(root) / "index.json"
    old = {}
    if path.exists() and not force:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("version") == INDEX_VERSION:
                old = d.get("files", {})
        except json.JSONDecodeError:
            pass
    files, reused, parsed = {}, 0, 0
    for rel, h in sorted(list_files(root).items()):
        if rel in old and old[rel].get("h") == h:
            files[rel] = old[rel]
            reused += 1
        else:
            files[rel] = {"h": h, **extract(root, rel)}
            parsed += 1
    names = set(files)
    stack = sorted({v for f, v in MANIFESTS.items() if f in names or any(n.endswith("/" + f) for n in names)})
    idx = {
        "version": INDEX_VERSION,
        "files": files,
        "stack": stack,
        "tests": sorted(f for f in files if TEST_RX.search(f)),
        "ci": sorted(f for f in files if f.startswith(".github/workflows/") or f in (".gitlab-ci.yml", "Jenkinsfile")),
        "ai_instruction_files": sorted(f for f in names if f in AI_FILES or f.startswith(".claude/")),
        "docs": sorted(f for f in files if f.lower().endswith(".md") and f.count("/") <= 1)[:60],
        "stats": {"files": len(files), "reused": reused, "parsed": parsed,
                  "symbols": sum(len(v["symbols"]) for v in files.values())},
    }
    atomic_write(path, json.dumps(idx, separators=(",", ":")))
    return idx


def load_index(root: Path) -> dict:
    p = state_dir(root) / "index.json"
    if not p.exists():
        return build(root)
    return json.loads(p.read_text(encoding="utf-8"))


def query(idx: dict, text: str, limit: int = 8) -> list[dict]:
    q = tokenize(text)
    qset = set(q)
    if not qset:
        return []
    results = []
    for rel, meta in idx["files"].items():
        ptoks = set(tokenize(rel))
        score = 3.0 * len(qset & ptoks)
        hits = []
        for name, kind, line in meta.get("symbols", []):
            st = set(tokenize(name))
            if qset & st:
                score += 2.0 * len(qset & st)
                hits.append({"symbol": name, "kind": kind, "line": line})
        if rel in idx["tests"]:
            score *= 0.8
        if score > 0 and not meta.get("skipped"):
            results.append({"path": rel, "score": round(score, 2), "lines": meta["lines"], "symbols": hits[:6],
                            "is_test": rel in idx["tests"]})
    results.sort(key=lambda r: (-r["score"], r["path"]))
    top = results[:limit]
    # attach tests whose basename relates to the selected source files
    for r in top:
        if r["is_test"]:
            continue
        stem = Path(r["path"]).stem.lower()
        r["tests"] = [t for t in idx["tests"] if stem and stem in Path(t).stem.lower()][:3]
    return top


def refs(root: Path, symbol: str, limit: int = 40) -> list[str]:
    if not re.fullmatch(r"[\w.$]+", symbol):
        raise ValueError("symbol must be an identifier")
    rc, out = run(["git", "grep", "-n", "-w", "-I", "--", symbol], cwd=root)
    if rc not in (0, 1):
        rc, out = run(["rg", "-n", "-w", "--", symbol], cwd=root)
    return out.splitlines()[:limit]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index"); i.add_argument("--force", action="store_true")
    q = sub.add_parser("query"); q.add_argument("text"); q.add_argument("--limit", type=int, default=8)
    r = sub.add_parser("refs"); r.add_argument("symbol")
    sub.add_parser("info")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    if a.cmd == "index":
        idx = build(root, a.force)
        print(json.dumps({"stack": idx["stack"], **idx["stats"], "tests": len(idx["tests"]), "ci": idx["ci"],
                          "ai_instruction_files": idx["ai_instruction_files"]}, indent=2))
    elif a.cmd == "query":
        print(json.dumps(query(load_index(root), a.text, a.limit), indent=2))
    elif a.cmd == "refs":
        print("\n".join(refs(root, a.symbol)) or "(no references)")
    else:
        idx = load_index(root)
        print(json.dumps({k: idx[k] for k in ("stack", "tests", "ci", "ai_instruction_files", "docs", "stats")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
