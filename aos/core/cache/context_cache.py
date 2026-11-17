"""Safe local response cache, duplicate-tool-call detection and prompt-prefix analysis.

Safety rules (enforced in code):
- Key includes a project-STATE FINGERPRINT (HEAD commit + uncommitted diff + referenced files + security policy) plus provider,
  model, params and prompt. Any change in code/policy/model => different key => miss. get() re-checks the stored fingerprint.
- Only deterministic calls (temperature 0) are cached unless explicitly allowed.
- Never stores prompts/responses containing secret patterns, or anything flagged confidential/shareable=False=>local only anyway;
  storage is under <project>/.aos/cache (git-ignored). There is no network or shared/public store.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import core  # noqa: F401
from aos_lib import atomic_write, contains_secret, load_config, run, state_dir


def _h(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "replace") + b"\0")
    return h.hexdigest()


def state_fingerprint(root: Path, files: list[str] | None = None, extra: str = "") -> str:
    rc, head = run(["git", "rev-parse", "HEAD"], cwd=root)
    _, diff = run(["git", "diff", "HEAD"], cwd=root, timeout=120)
    _, untracked = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root)
    parts = [head.strip() if rc == 0 else "nogit", _h(diff)]
    for rel in sorted(set((files or []) + [u for u in untracked.splitlines()[:200]])):
        p = root / rel
        if p.is_file() and p.stat().st_size < 2_000_000:
            parts.append(rel + ":" + hashlib.sha256(p.read_bytes()).hexdigest())
    sec = root / ".aos" / "config" / "security.yaml"
    parts.append(_h(sec.read_text() if sec.exists() else "default-security"))
    return _h(*parts, extra)


def make_key(provider: str, model: str, prompt: str, params: dict, fingerprint: str, permissions: str = "") -> str:
    return _h(provider, model, json.dumps(params, sort_keys=True), prompt, fingerprint, permissions)


def _dir(root: Path) -> Path:
    d = state_dir(root) / "cache"
    d.mkdir(exist_ok=True)
    return d


def _stats(root: Path, **inc) -> dict:
    p = _dir(root) / "stats.json"
    s = json.loads(p.read_text()) if p.exists() else {"hits": 0, "misses": 0, "stores": 0, "refused": 0}
    for k, v in inc.items():
        s[k] = s.get(k, 0) + v
    atomic_write(p, json.dumps(s))
    return s


def put(root: Path, key: str, prompt: str, response: str, *, fingerprint: str, params: dict, provider: str, model: str,
        ttl_s: int | None = None, confidential: bool = False, allow_nondeterministic: bool = False) -> dict:
    cfg = load_config("routing", root)["cache"]
    why = None
    if confidential:
        why = "flagged confidential"
    elif contains_secret(prompt) or contains_secret(response):
        why = "contains secret-like content"
    elif cfg.get("require_temperature_zero", True) and not allow_nondeterministic and params.get("temperature", 1) != 0:
        why = "non-deterministic call (temperature != 0)"
    if why:
        _stats(root, refused=1)
        return {"stored": False, "reason": why}
    d = _dir(root) / key[:2]
    atomic_write(d / f"{key}.json", json.dumps({"key": key, "fingerprint": fingerprint, "provider": provider, "model": model, "response": response,
                                              "created": time.time(), "ttl_s": ttl_s or cfg["default_ttl_s"], "prompt_chars": len(prompt)}))
    _stats(root, stores=1)
    entries = sorted((_dir(root)).glob("*/*.json"), key=lambda p: p.stat().st_mtime)
    for old in entries[: max(0, len(entries) - cfg["max_entries"])]:
        old.unlink(missing_ok=True)
    return {"stored": True}


def get(root: Path, key: str, fingerprint: str) -> dict | None:
    p = _dir(root) / key[:2] / f"{key}.json"
    if not p.exists():
        _stats(root, misses=1)
        return None
    e = json.loads(p.read_text())
    if e["fingerprint"] != fingerprint or time.time() - e["created"] > e["ttl_s"]:
        p.unlink(missing_ok=True)
        _stats(root, misses=1)
        return None
    _stats(root, hits=1, saved_tokens_est=(e.get("prompt_chars", 0) + len(e["response"])) // 4)   # ESTIMATE (chars/4)
    return e


def clear(root: Path) -> int:
    n = 0
    for p in _dir(root).glob("*/*.json"):
        p.unlink()
        n += 1
    return n


def stats(root: Path) -> dict:
    s = _stats(root)
    tot = s["hits"] + s["misses"]
    return {**s, "hit_rate": round(s["hits"] / tot, 3) if tot else None, "entries": sum(1 for _ in _dir(root).glob("*/*.json"))}


def check_duplicate(root: Path, tool: str, args: dict, fingerprint: str) -> dict:
    """Detect an identical tool call made against identical project state (wasted work). First call returns duplicate=False."""
    f = _dir(root) / f"calls-{fingerprint[:16]}.jsonl"
    sig = _h(tool, json.dumps(args, sort_keys=True))
    seen = set(f.read_text().split()) if f.exists() else set()
    if sig in seen:
        _stats(root, duplicate_calls=1)
        return {"duplicate": True, "advice": "same tool+args on unchanged state: reuse the previous result"}
    with f.open("a") as fh:
        fh.write(sig + "\n")
    return {"duplicate": False}


def stable_prefix_ratio(prompts: list[str]) -> float:
    """Fraction of the first prompt shared as a prefix by ALL prompts: the part a provider prompt cache could reuse (estimate)."""
    if len(prompts) < 2 or not prompts[0]:
        return 0.0
    n = 0
    for chars in zip(*prompts):
        if len(set(chars)) != 1:
            break
        n += 1
    return round(n / len(prompts[0]), 3)
