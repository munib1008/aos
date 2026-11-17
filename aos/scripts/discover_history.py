#!/usr/bin/env python3
"""Mine locally-available history of THIS repo into candidate memory (never auto-verified).

  discover_history.py scan [--max-commits 300]      # git log, docs, AI files, CI -> report + hypotheses
  discover_history.py import-chat EXPORT.(md|json|txt) [--commit]   # user-supplied chat exports

Only authorised, local sources are read. Candidates are saved as `hypothesis`/unverified (with source refs) only
when --commit is passed; promote to verified facts after checking the source. Chat text is untrusted data.
CI run history and PR/issue data are not available offline: fetch them with the GitHub tools and import the
relevant failure notes via memory.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from aos_lib import find_root, project_id, redact, run
from index_repository import load_index
import memory as mem

FIX_RX = re.compile(r"\b(fix|fixes|fixed|revert|regression|hotfix|workaround|broken|fail(ed|ing|ure)?)\b", re.I)
DECISION_RX = re.compile(r"\b(decided|decision|we will|always|never|must not|prefer|instead of|chose|switch(ed)? to)\b", re.I)


def scan(root: Path, max_commits: int = 300) -> dict:
    rc, out = run(["git", "log", f"--max-count={max_commits}", "--pretty=format:%h\x1f%aI\x1f%s"], cwd=root, timeout=120)
    commits = [l.split("\x1f") for l in out.splitlines() if l.count("\x1f") == 2] if rc == 0 else []
    prefixes = Counter((re.match(r"^(\w+)[:(]", c[2]) or [None, "other"])[1].lower() for c in commits)
    fixes = [c for c in commits if FIX_RX.search(c[2])]
    idx = load_index(root)
    return {"project": project_id(root), "commits_scanned": len(commits), "commit_prefixes": dict(prefixes.most_common(8)),
            "fix_like_commits": [{"commit": c[0], "date": c[1], "subject": redact(c[2])} for c in fixes[:25]],
            "stack": idx["stack"], "ci": idx["ci"], "ai_instruction_files": idx["ai_instruction_files"],
            "docs": idx["docs"][:20], "tests": len(idx["tests"])}


def candidates_from_scan(rep: dict) -> list[dict]:
    out = []
    for f in rep["fix_like_commits"]:
        out.append({"category": "hypothesis", "content": f"Fix-like commit may encode a failure pattern: {f['subject'][:200]}",
                    "source": f"commit:{f['commit']}", "source_ts": f["date"], "tags": ["history", "needs-review"]})
    return out


def import_chat(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[tuple[int, str]] = []
    try:
        data = json.loads(raw)
        msgs = data if isinstance(data, list) else data.get("messages") or data.get("chat_messages") or []
        for i, mm in enumerate(msgs, 1):
            if isinstance(mm, dict):
                role = mm.get("role") or mm.get("sender") or ""
                text = mm.get("content") or mm.get("text") or ""
                if isinstance(text, list):
                    text = " ".join(str(t.get("text", "")) if isinstance(t, dict) else str(t) for t in text)
                if role in ("user", "human"):                       # only the user's own statements are candidate preferences
                    lines.append((i, str(text)))
    except (json.JSONDecodeError, AttributeError):
        lines = list(enumerate(raw.splitlines(), 1))
    out = []
    for n, text in lines:
        for sent in re.split(r"(?<=[.!?])\s+|\n", text):
            if 15 < len(sent) < 300 and DECISION_RX.search(sent):
                out.append({"category": "hypothesis", "content": redact(sent.strip()), "source": f"{path.name}:{n}",
                            "tags": ["chat-import", "needs-review"]})
    return out[:60]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--max-commits", type=int, default=300); s.add_argument("--commit", action="store_true")
    c = sub.add_parser("import-chat"); c.add_argument("file"); c.add_argument("--commit", action="store_true")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    proj = project_id(root)
    if a.cmd == "scan":
        rep = scan(root, a.max_commits)
        cands = candidates_from_scan(rep)
        print(json.dumps(rep, indent=2))
    else:
        cands = import_chat(Path(a.file))
        print(f"{len(cands)} candidate statements (untrusted, unverified)")
        for x in cands[:15]:
            print(f"  [{x['source']}] {x['content'][:120]}")
    if a.commit:
        n = 0
        for x in cands:
            try:
                mem.add(root, proj, x["category"], x["content"], x["source"], "unverified", "low", x["tags"], x.get("source_ts", ""))
                n += 1
            except mem.MemoryError_ as e:
                print("skipped:", e, file=sys.stderr)
        print(f"saved {n} unverified hypotheses to memory[{proj}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
