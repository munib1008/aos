"""Shared helpers for AOS scripts. Standard library only."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

AOS_DIR = Path(__file__).resolve().parent.parent  # the aos/ package (or .claude/aos/)

# ---------------------------------------------------------------- YAML subset
_SCALAR_INT = re.compile(r"^-?\d+$")
_SCALAR_FLOAT = re.compile(r"^-?\d+\.\d+$")


def _scalar(tok: str):
    tok = tok.strip()
    if tok == "" or tok in ("null", "~"):
        return None
    if tok in ("true", "True"):
        return True
    if tok in ("false", "False"):
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    if _SCALAR_INT.match(tok):
        return int(tok)
    if _SCALAR_FLOAT.match(tok):
        return float(tok)
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        return [] if not inner else [_scalar(p) for p in inner.split(",")]
    if tok.startswith("{") and tok.endswith("}"):
        inner = tok[1:-1].strip()
        out = {}
        for part in ([] if not inner else inner.split(",")):
            k, _, v = part.partition(":")
            out[_key(k)] = _scalar(v)
        return out
    return tok


def _key(k: str):
    k = k.strip().strip("\"'")
    return int(k) if _SCALAR_INT.match(k) else k


def _strip_comment(line: str) -> str:
    out, q = [], None
    for ch in line:
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def parse_yaml(text: str) -> dict:
    """Parse the YAML subset AOS configs use: nested maps (2-space indent),
    lists of scalars (`- x` or `[a, b]`), scalars, comments. No anchors/multiline."""
    lines = []
    for raw in text.splitlines():
        s = _strip_comment(raw)
        if s.strip():
            lines.append((len(s) - len(s.lstrip(" ")), s.strip()))

    def block(i: int, indent: int):
        if i < len(lines) and lines[i][1].startswith("- "):
            items = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                items.append(_scalar(lines[i][1][2:]))
                i += 1
            return items, i
        d = {}
        while i < len(lines) and lines[i][0] == indent:
            content = lines[i][1]
            if ":" not in content:
                raise ValueError(f"bad YAML line: {content!r}")
            key, _, rest = content.partition(":")
            key, rest = _key(key), rest.strip()
            i += 1
            if rest == "":
                if i < len(lines) and lines[i][0] > indent:
                    d[key], i = block(i, lines[i][0])
                else:
                    d[key] = None
            else:
                d[key] = _scalar(rest)
        if i < len(lines) and lines[i][0] > indent:
            raise ValueError(f"unexpected indent near {lines[i][1]!r}")
        return d, i

    if not lines:
        return {}
    result, _ = block(0, lines[0][0])
    return result


def load_config(name: str, root: Path | None = None) -> dict:
    """Load config/<name>.yaml; a project-level .aos/config/<name>.yaml overrides keys (shallow per top key)."""
    base = parse_yaml((AOS_DIR / "config" / f"{name}.yaml").read_text(encoding="utf-8"))
    if root:
        ov = root / ".aos" / "config" / f"{name}.yaml"
        if ov.exists():
            for k, v in parse_yaml(ov.read_text(encoding="utf-8")).items():
                base[k] = {**base.get(k, {}), **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


# ------------------------------------------------------------------ project
def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def find_root(start: str | Path | None = None) -> Path:
    start = Path(start or os.getcwd()).resolve()
    rc, out = run(["git", "rev-parse", "--show-toplevel"], cwd=start)
    return Path(out.strip()) if rc == 0 and out.strip() else start


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "project"


def project_id(root: Path) -> str:
    """Resolve project id via projects.yaml (match by repo suffix of origin URL), else directory slug."""
    rc, url = run(["git", "config", "--get", "remote.origin.url"], cwd=root)
    url = url.strip().lower() if rc == 0 else ""
    try:
        projects = load_config("projects", root).get("projects", {}) or {}
    except (OSError, ValueError):
        projects = {}
    for pid, meta in projects.items():
        repo = str((meta or {}).get("repo") or "").lower()
        if repo and url and (url.endswith(repo) or url.endswith(repo + ".git")):
            return pid
        if (meta or {}).get("path") and Path(meta["path"]).resolve() == root:
            return pid
    return slug(root.name)


def state_dir(root: Path) -> Path:
    d = root / ".aos"
    d.mkdir(exist_ok=True)
    return d


def git_state(root: Path) -> dict:
    def g(*a):
        rc, o = run(["git", *a], cwd=root)
        return o.strip() if rc == 0 else ""
    return {
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": g("rev-parse", "HEAD"),
        "dirty_files": [l[3:] for l in g("status", "--porcelain").splitlines()],
        "remote": redact(g("config", "--get", "remote.origin.url")),
    }


# --------------------------------------------------------------------- misc
_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"://([^/\s:@]+):([^/\s@]+)@"), r"://\1:[REDACTED]@"),
    (re.compile(r"(?i)\b([A-Za-z_]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)[A-Za-z_]*)(\s*[:=]\s*)(['\"]?)[^\s'\",;]{6,}\3"),
     r"\1\2[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, rep in _SECRET_PATTERNS:
        text = pat.sub(rep, text)
    return text


def contains_secret(text: str) -> bool:
    return redact(text) != text


def est_tokens(text: str) -> int:
    """ESTIMATE only (~4 chars/token). Real counts come from provider usage data."""
    return math.ceil(len(text) / 4)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: invalid JSON ({e})") from e
    return out


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tokenize(text: str) -> list[str]:
    """Split identifiers/paths into lowercase words (camelCase, snake_case, paths)."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if len(t) > 2]
