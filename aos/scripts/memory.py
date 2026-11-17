#!/usr/bin/env python3
"""Per-project persistent memory (append-only JSONL, last event per id wins).

  memory.py add --category verified_fact --content "..." --source README.md:3 --verification verified --tags stack
  memory.py search "supabase auth" --limit 5 --budget 800
  memory.py supersede ID --content "..." --source ...
  memory.py deprecate ID --reason "..."   |  memory.py rollback ID
  memory.py render        -> PROJECT_MEMORY.md, DECISIONS.md, ... under .aos/memory/<project>/
  memory.py stats

Project memory is isolated: records live under .aos/memory/<project>/ and are only queried for that project.
The pseudo-project `_global` accepts only cross-project categories (preferences, generic patterns).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from aos_lib import (append_jsonl, atomic_write, contains_secret, est_tokens, find_root, load_config, now_iso,
                     project_id, read_jsonl, state_dir, tokenize)

CATEGORIES = ["verified_fact", "decision", "user_preference", "reusable_pattern", "verified_solution",
              "confirmed_failure", "hypothesis", "known_limitation", "rejected_approach", "pending_decision",
              "deprecated", "lesson_general"]
# Categories that assert truth: must be verified (or stated by the user), never AI-suggested.
ASSERTIVE = {"verified_fact", "decision", "verified_solution", "confirmed_failure", "known_limitation"}
VERIFICATIONS = ["verified", "user_stated", "unverified", "ai_suggested"]
CONFIDENCE = ["high", "medium", "low"]
GLOBAL = "_global"

RENDER = {
    "PROJECT_MEMORY.md": ({"verified_fact", "known_limitation", "user_preference"}, None),
    "PROJECT_ARCHITECTURE.md": (None, "architecture"),
    "DECISIONS.md": ({"decision", "rejected_approach", "pending_decision"}, None),
    "LESSONS_LEARNED.md": ({"verified_solution", "reusable_pattern", "lesson_general"}, None),
    "KNOWN_FAILURES.md": ({"confirmed_failure"}, None),
    "TEST_BASELINE.md": (None, "test-baseline"),
    "OPEN_TASKS.md": ({"pending_decision", "hypothesis"}, "task"),
}


class MemoryError_(ValueError):
    pass


def store_path(root: Path, project: str) -> Path:
    return state_dir(root) / "memory" / project / "records.jsonl"


def load_state(root: Path, project: str) -> dict[str, dict]:
    """Collapse the event log: last record per id, then mark superseded ones."""
    state: dict[str, dict] = {}
    for rec in read_jsonl(store_path(root, project)):
        if rec.get("project") != project:
            raise MemoryError_(f"isolation violation: record {rec.get('id')} belongs to {rec.get('project')!r}")
        state[rec["id"]] = rec
    for rec in list(state.values()):
        sup = rec.get("supersedes")
        if sup and sup in state and rec.get("status", "active") in ("active", "deprecated"):   # a reverted successor restores the old record
            state[sup] = {**state[sup], "status": "deprecated", "superseded_by": rec["id"]}
    return state


def make_id(project: str, category: str, content: str) -> str:
    return "m-" + hashlib.sha1(f"{project}|{category}|{content.strip().lower()}".encode()).hexdigest()[:10]


def validate_new(rec: dict, global_cats: list[str]) -> None:
    if rec["category"] not in CATEGORIES:
        raise MemoryError_(f"unknown category {rec['category']!r}; use one of {CATEGORIES}")
    if rec["verification"] not in VERIFICATIONS or rec["confidence"] not in CONFIDENCE:
        raise MemoryError_("invalid verification/confidence value")
    if not rec["content"].strip() or not rec["source"].strip():
        raise MemoryError_("content and source are both required")
    if contains_secret(rec["content"]) or contains_secret(rec["source"]):
        raise MemoryError_("refused: content looks like it contains a secret/token/password")
    if rec["category"] in ASSERTIVE and rec["verification"] not in ("verified", "user_stated"):
        raise MemoryError_(f"category {rec['category']!r} requires verification verified|user_stated "
                           "(AI suggestions must be saved as 'hypothesis')")
    if len(rec["content"]) > 600:
        raise MemoryError_("content too long (>600 chars): store a compact record and reference the source")
    if rec["project"] == GLOBAL and rec["category"] not in global_cats:
        raise MemoryError_(f"_global memory accepts only {global_cats}; project facts must stay project-scoped")


def add(root: Path, project: str, category: str, content: str, source: str, verification: str = "unverified",
        confidence: str = "medium", tags: list[str] | None = None, source_ts: str | None = None,
        supersedes: str | None = None) -> dict:
    rec = {"id": make_id(project, category, content), "project": project, "category": category,
           "content": content.strip(), "source": source.strip(), "source_ts": source_ts or "",
           "verification": verification, "confidence": confidence, "tags": sorted(set(tags or [])),
           "last_validated": now_iso() if verification in ("verified", "user_stated") else "",
           "created": now_iso(), "status": "active", "supersedes": supersedes}
    validate_new(rec, load_config("optimization", root)["memory"]["global_allowed_categories"])
    state = load_state(root, project)
    if rec["id"] in state and state[rec["id"]].get("status") == "active":
        return {**state[rec["id"]], "_dedup": True}
    if supersedes and supersedes not in state:
        raise MemoryError_(f"cannot supersede unknown id {supersedes}")
    append_jsonl(store_path(root, project), rec)
    return rec


def set_status(root: Path, project: str, rid: str, status: str, reason: str = "") -> dict:
    state = load_state(root, project)
    if rid not in state:
        raise MemoryError_(f"unknown id {rid}")
    rec = {**state[rid], "status": status, "status_reason": reason, "status_changed": now_iso()}
    rec.pop("superseded_by", None)
    append_jsonl(store_path(root, project), rec)
    return rec


def rollback(root: Path, project: str, rid: str) -> dict:
    """Undo the last event for an id by dropping it from the log (log is the audit trail: we mark reverted instead)."""
    return set_status(root, project, rid, "reverted", "rolled back by user")


def search(root: Path, project: str, query: str, limit: int = 8, budget: int | None = None,
           include_inactive: bool = False, categories: set[str] | None = None) -> list[dict]:
    q = set(tokenize(query))
    exact = query.strip().lower()
    stale_days = load_config("optimization", root)["memory"]["stale_after_days"]
    weights = {"verified": 3, "user_stated": 3, "unverified": 1, "ai_suggested": 0}
    out = []
    for rec in load_state(root, project).values():
        if not include_inactive and rec.get("status", "active") != "active":
            continue
        if categories and rec["category"] not in categories:
            continue
        hay = " ".join([rec["content"], rec["source"], " ".join(rec.get("tags", [])), rec["category"], rec["id"]])
        words = set(tokenize(hay))
        score = 0.0
        if exact and exact in hay.lower():
            score += 5          # deterministic exact match (ids, commits, error text, filenames)
        score += 2.0 * len(q & words) / (len(q) or 1) * 3
        if score == 0:
            continue
        score += weights.get(rec["verification"], 0) * 0.5
        lv = rec.get("last_validated") or rec.get("created", "")
        try:
            age = (datetime.now(timezone.utc) - datetime.strptime(lv, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days
            if age > stale_days:
                rec = {**rec, "stale": True}
                score -= 0.5
            else:
                score += 0.3
        except ValueError:
            pass
        out.append((score, rec))
    out.sort(key=lambda x: (-x[0], x[1]["id"]))
    res, used = [], 0
    for _, rec in out[:limit]:
        cost = est_tokens(json.dumps(rec))
        if budget is not None and used + cost > budget and res:
            break
        used += cost
        res.append(rec)
    return res


def fmt(rec: dict) -> str:
    flags = "" if rec.get("status", "active") == "active" else f" [{rec['status']}]"
    flags += " [stale]" if rec.get("stale") else ""
    return (f"- **{rec['category']}** ({rec['verification']}, {rec['confidence']}){flags}: {rec['content']}  \n"
            f"  _src: {rec['source']}{(' @ ' + rec['source_ts']) if rec.get('source_ts') else ''}; id {rec['id']}; "
            f"validated {rec.get('last_validated') or 'never'}_")


def _belongs(fname: str, rec: dict) -> bool:
    cats, tag = RENDER[fname]
    tags = rec.get("tags", [])
    if fname == "PROJECT_MEMORY.md":   # keep architecture/test-baseline facts in their own files
        return rec["category"] in cats and "architecture" not in tags and "test-baseline" not in tags
    if fname == "OPEN_TASKS.md":
        return rec["category"] in cats or tag in tags
    if cats is None:
        return tag in tags
    return rec["category"] in cats


def render(root: Path, project: str) -> list[Path]:
    state = [r for r in load_state(root, project).values() if r.get("status", "active") == "active"]
    outdir = store_path(root, project).parent
    written = []
    for fname in RENDER:
        rows = sorted((r for r in state if _belongs(fname, r)), key=lambda r: (r["category"], r["created"]))
        body = "\n".join(fmt(r) for r in rows) or "_No records yet._"
        text = (f"# {fname[:-3].replace('_', ' ').title()} - {project}\n\n"
                f"_Generated by AOS from `records.jsonl`. Do not edit by hand; use `memory.py`._\n\n{body}\n")
        p = outdir / fname
        atomic_write(p, text)
        written.append(p)
    return written


def stats(root: Path, project: str) -> dict:
    st = load_state(root, project)
    by: dict[str, int] = {}
    for r in st.values():
        k = f"{r['category']}/{r.get('status', 'active')}/{r['verification']}"
        by[k] = by.get(k, 0) + 1
    return {"project": project, "records": len(st), "breakdown": by}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    ap.add_argument("--project", help="project id (default: resolved from repo); use _global for cross-project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_ = sub.add_parser("add")
    a_.add_argument("--category", required=True, choices=CATEGORIES)
    a_.add_argument("--content", required=True)
    a_.add_argument("--source", required=True)
    a_.add_argument("--verification", default="unverified", choices=VERIFICATIONS)
    a_.add_argument("--confidence", default="medium", choices=CONFIDENCE)
    a_.add_argument("--tags", default="")
    a_.add_argument("--source-ts", default="")
    s_ = sub.add_parser("search")
    s_.add_argument("query")
    s_.add_argument("--limit", type=int, default=8)
    s_.add_argument("--budget", type=int, help="max estimated tokens to return")
    s_.add_argument("--all", action="store_true", help="include deprecated/reverted")
    s_.add_argument("--json", action="store_true")
    sp = sub.add_parser("supersede")
    sp.add_argument("id"); sp.add_argument("--content", required=True); sp.add_argument("--source", required=True)
    sp.add_argument("--category", required=True, choices=CATEGORIES)
    sp.add_argument("--verification", default="verified", choices=VERIFICATIONS)
    d_ = sub.add_parser("deprecate"); d_.add_argument("id"); d_.add_argument("--reason", default="")
    r_ = sub.add_parser("rollback"); r_.add_argument("id")
    sub.add_parser("render"); sub.add_parser("stats")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    proj = a.project or project_id(root)
    try:
        if a.cmd == "add":
            r = add(root, proj, a.category, a.content, a.source, a.verification, a.confidence,
                    [t for t in a.tags.split(",") if t], a.source_ts)
            print(("exists " if r.get("_dedup") else "added ") + r["id"])
        elif a.cmd == "search":
            res = search(root, proj, a.query, a.limit, a.budget, a.all)
            print(json.dumps(res, indent=2) if a.json else "\n".join(fmt(r) for r in res) or "(no matching memory)")
        elif a.cmd == "supersede":
            r = add(root, proj, a.category, a.content, a.source, a.verification, supersedes=a.id)
            print("superseded", a.id, "with", r["id"])
        elif a.cmd == "deprecate":
            print("deprecated", set_status(root, proj, a.id, "deprecated", a.reason)["id"])
        elif a.cmd == "rollback":
            print("reverted", rollback(root, proj, a.id)["id"])
        elif a.cmd == "render":
            print("\n".join(str(p) for p in render(root, proj)))
        elif a.cmd == "stats":
            print(json.dumps(stats(root, proj), indent=2))
    except MemoryError_ as e:
        print(f"memory error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
