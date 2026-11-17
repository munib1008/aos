#!/usr/bin/env python3
"""Portable session manifests: new / fork / resume / update / archive / health / merge-check.

Manifests live in .aos/sessions/<id>/manifest.json and are provider-neutral. A session fork is NOT a git
branch: implementation forks get their own branch/worktree (only when --worktree is passed).
Nothing here merges code automatically.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

from aos_lib import atomic_write, find_root, git_state, load_config, now_iso, project_id, redact, run, state_dir
from route_task import route


def sdir(root: Path) -> Path:
    d = state_dir(root) / "sessions"
    d.mkdir(exist_ok=True)
    return d


def path_of(root: Path, sid: str) -> Path:
    return sdir(root) / sid / "manifest.json"


def load(root: Path, sid: str) -> dict:
    if sid == "latest":
        ms = sorted(sdir(root).glob("*/manifest.json"), key=lambda p: p.stat().st_mtime)
        ms = [m for m in ms if json.loads(m.read_text()).get("status") != "archived"]
        if not ms:
            raise SystemExit("no active session")
        return json.loads(ms[-1].read_text(encoding="utf-8"))
    p = path_of(root, sid)
    if not p.exists():
        raise SystemExit(f"unknown session {sid}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(root: Path, m: dict) -> None:
    m["updated"] = now_iso()
    atomic_write(path_of(root, m["session_id"]), json.dumps(json.loads(redact(json.dumps(m))), indent=2, sort_keys=True))


def new(root: Path, task: str, criteria: str = "", kind: str = "task", parent: str | None = None,
        files: list[str] | None = None, provider: str = "claude", model_override: dict | None = None) -> dict:
    g = git_state(root)
    r = route(task, root=root)
    m = {
        "session_id": f"s-{time.strftime('%y%m%d-%H%M%S')}-{secrets.token_hex(2)}", "parent_session": parent, "kind": kind,
        "project": project_id(root), "task_id": f"t-{secrets.token_hex(3)}", "status": "active", "created": now_iso(),
        "branch": g["branch"], "base_commit": g["commit"], "worktree": str(root), "dirty_at_start": g["dirty_files"],
        "provider": provider, "model_class": (model_override or r)["tier"], "model": (model_override or r)["model"],
        "reasoning_effort": r["effort"], "level": r["level"], "objective": task, "requirements": [criteria] if criteria else [],
        "planned_files": files or [], "files_modified": [], "decisions": [], "tests": [], "completed": [], "pending": [],
        "blockers": [], "handoff_status": "none",
    }
    save(root, m)
    return m


def fork(root: Path, parent_id: str, task: str, kind: str, files: list[str], worktree: bool, criteria: str = "") -> dict:
    parent = load(root, parent_id)
    if kind not in ("analysis", "implementation"):
        raise SystemExit("kind must be analysis|implementation")
    if kind == "implementation":
        for s in sdir(root).glob("*/manifest.json"):
            o = json.loads(s.read_text())
            if o["status"] == "active" and o["kind"] == "implementation" and o["session_id"] != parent["session_id"]:
                clash = set(o["planned_files"]) & set(files)
                if clash:
                    raise SystemExit(f"refused: files already claimed by active session {o['session_id']}: {sorted(clash)}")
    child = new(root, task, criteria, kind, parent["session_id"], files)
    child["requirements"] = parent["requirements"] + child["requirements"]
    child["decisions"] = list(parent["decisions"])       # compact inheritance, not the conversation
    child["inherited_dirty_files"] = git_state(root)["dirty_files"]
    if kind == "implementation" and worktree:
        branch = f"aos/{child['session_id']}"
        wt = str(root.parent / f"{root.name}-{child['session_id']}")
        rc, out = run(["git", "worktree", "add", "-b", branch, wt, parent["base_commit"] or "HEAD"], cwd=root)
        if rc != 0:
            raise SystemExit(f"git worktree failed: {out.strip()}")
        child.update(branch=branch, worktree=wt)
    save(root, child)
    return child


def resume(root: Path, sid: str) -> tuple[dict, list[str]]:
    m = load(root, sid)
    g = git_state(Path(m["worktree"]) if Path(m["worktree"]).exists() else root)
    warn = []
    if g["branch"] != m["branch"]:
        warn.append(f"branch changed: recorded {m['branch']} now {g['branch']}")
    if m["base_commit"] and g["commit"] != m["base_commit"]:
        rc, _ = run(["git", "merge-base", "--is-ancestor", m["base_commit"], g["commit"]], cwd=root)
        warn.append("HEAD moved ahead of base commit (verify new commits)" if rc == 0 else "HEAD is NOT a descendant of base commit (history rewritten/other branch)")
    unexpected = sorted(set(g["dirty_files"]) - set(m["files_modified"]) - set(m.get("dirty_at_start", [])))
    if unexpected:
        warn.append(f"uncommitted files not recorded in manifest: {unexpected[:10]}")
    for t in m["tests"]:
        if t.get("status") != "PASSED":
            warn.append(f"last recorded test run was {t.get('status')}: {t.get('command')}")
    return m, warn


def health(root: Path, a) -> dict:
    cfg = load_config("optimization", root)["session"]
    reasons, action = [], "continue"
    if a.window_frac is not None:
        if a.window_frac >= cfg["compact_strongly_at"]:
            action = "compact"; reasons.append(f"context ~{a.window_frac:.0%} (estimate) >= {cfg['compact_strongly_at']:.0%}")
        elif a.window_frac >= cfg["compact_advice_at"]:
            action = "consider-compact"; reasons.append(f"context ~{a.window_frac:.0%} (estimate)")
    if a.large_outputs >= 3:
        reasons.append(f"{a.large_outputs} large tool outputs in context: summarize via summarize_tests.py and drop raw output")
        action = action if action == "compact" else "consider-compact"
    if a.failures >= cfg["repeated_failures_for_fork"]:
        reasons.append(f"{a.failures} failed attempts: fork an analysis session with a fresh hypothesis (do not retry identically)")
        action = "fork-analysis"
    if a.topic_change:
        reasons.append("topic changed: start a clean task session; persist lessons first")
        action = "new-session" if action == "continue" else action
    if a.milestone:
        reasons.append("clean milestone reached: safe point to compact or fork next independent task")
    pre = ["persist task brief + decisions (session_manager.py update)", "record tests status", "record git branch/commit/dirty files"] if action != "continue" else []
    return {"action": action, "reasons": reasons or ["no trigger met"], "before_acting": pre, "note": "indicators are estimates unless the client exposes token counts"}


def merge_check(root: Path, sid: str) -> dict:
    c = load(root, sid)
    p = load(root, c["parent_session"]) if c.get("parent_session") else None
    issues = []
    if not c["tests"] or c["tests"][-1].get("status") != "PASSED":
        issues.append("no passing test run recorded for child")
    if p:
        overlap = set(c["files_modified"]) & set(p["files_modified"])
        if overlap:
            issues.append(f"both sessions modified: {sorted(overlap)} (manual conflict review)")
    return {"session": sid, "ready_for_human_review": not issues, "issues": issues, "branch": c["branch"],
            "note": "AOS never merges automatically"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new"); n.add_argument("task"); n.add_argument("--criteria", default=""); n.add_argument("--files", default="")
    f = sub.add_parser("fork"); f.add_argument("parent"); f.add_argument("task"); f.add_argument("--kind", default="analysis")
    f.add_argument("--files", default=""); f.add_argument("--worktree", action="store_true"); f.add_argument("--criteria", default="")
    r = sub.add_parser("resume"); r.add_argument("id", nargs="?", default="latest")
    u = sub.add_parser("update"); u.add_argument("id")
    for k in ("done", "pending", "decision", "blocker", "file"):
        u.add_argument(f"--{k}", action="append", default=[])
    u.add_argument("--tests-json", help="JSON from summarize_tests.py --json")
    ar = sub.add_parser("archive"); ar.add_argument("id")
    h = sub.add_parser("health")
    h.add_argument("--window-frac", type=float); h.add_argument("--large-outputs", type=int, default=0)
    h.add_argument("--failures", type=int, default=0); h.add_argument("--topic-change", action="store_true"); h.add_argument("--milestone", action="store_true")
    mc = sub.add_parser("merge-check"); mc.add_argument("id")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    if a.cmd == "new":
        m = new(root, a.task, a.criteria, files=[x for x in a.files.split(",") if x]); print(m["session_id"])
    elif a.cmd == "fork":
        m = fork(root, a.parent, a.task, a.kind, [x for x in a.files.split(",") if x], a.worktree, a.criteria)
        print(m["session_id"], "branch", m["branch"], "worktree", m["worktree"])
    elif a.cmd == "resume":
        m, warn = resume(root, a.id)
        print(f"Session {m['session_id']} [{m['status']}] project={m['project']} level={m['level']} model={m['model']} effort={m['reasoning_effort']}")
        print(f"Objective: {m['objective']}\nBranch {m['branch']} @ {m['base_commit'][:10]}")
        for k in ("requirements", "decisions", "completed", "pending", "blockers", "files_modified"):
            if m[k]:
                print(f"{k}: " + "; ".join(map(str, m[k])))
        for t in m["tests"][-3:]:
            print(f"tests: {t.get('status')} {t.get('command')} -> {t.get('full_log')}")
        for w in warn:
            print("WARNING:", w)
        print("Verify completed work before relying on it; re-run tests listed above.")
    elif a.cmd == "update":
        m = load(root, a.id)
        m["completed"] += a.done; m["pending"] += a.pending; m["decisions"] += a.decision; m["blockers"] += a.blocker
        m["files_modified"] = sorted(set(m["files_modified"]) | set(a.file))
        m["pending"] = [p for p in m["pending"] if p not in a.done]
        if a.tests_json:
            t = json.loads(a.tests_json); m["tests"].append({k: t.get(k) for k in ("command", "status", "total", "passed", "failed", "failure_ids", "full_log")})
        save(root, m); print("updated", m["session_id"])
    elif a.cmd == "archive":
        m = load(root, a.id); m["status"] = "archived"; save(root, m); print("archived", a.id)
    elif a.cmd == "health":
        print(json.dumps(health(root, a), indent=2))
    elif a.cmd == "merge-check":
        print(json.dumps(merge_check(root, a.id), indent=2))
    else:
        for p in sorted(sdir(root).glob("*/manifest.json")):
            m = json.loads(p.read_text()); print(m["session_id"], m["status"], m["kind"], m["parent_session"] or "-", m["objective"][:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
