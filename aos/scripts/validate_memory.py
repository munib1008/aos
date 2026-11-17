#!/usr/bin/env python3
"""Validate a project's memory store: schema, secrets, isolation, source references, contradictions.

  validate_memory.py [--project ID] [--check-sources] [--stale]
Exit 0 = clean, 1 = problems (listed). With --check-sources, file/commit sources are checked against the repo.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from aos_lib import contains_secret, find_root, load_config, project_id, read_jsonl, run
import memory as mem


def validate(root: Path, project: str, check_sources: bool = False) -> list[str]:
    problems: list[str] = []
    path = mem.store_path(root, project)
    try:
        events = read_jsonl(path)
    except ValueError as e:
        return [str(e)]
    glob_cats = load_config("optimization", root)["memory"]["global_allowed_categories"]
    seen_content: dict[tuple, str] = {}
    for rec in events:
        rid = rec.get("id", "?")
        for f in ("id", "project", "category", "content", "source", "verification", "confidence"):
            if not rec.get(f):
                problems.append(f"{rid}: missing field {f}")
        if rec.get("project") != project:
            problems.append(f"{rid}: ISOLATION project {rec.get('project')!r} in store of {project!r}")
        if contains_secret(rec.get("content", "")) or contains_secret(rec.get("source", "")):
            problems.append(f"{rid}: contains secret-like text")
        if rec.get("category") in mem.ASSERTIVE and rec.get("verification") not in ("verified", "user_stated"):
            problems.append(f"{rid}: {rec.get('category')} not verified")
        if project == mem.GLOBAL and rec.get("category") not in glob_cats:
            problems.append(f"{rid}: category not allowed in _global")
    try:
        state = mem.load_state(root, project)
    except mem.MemoryError_ as e:
        return problems + [f"ISOLATION: {e}"]
    for rid, rec in state.items():
        if rec.get("status", "active") != "active":
            continue
        key = (rec["category"], re.sub(r"\W+", " ", rec["content"].lower()).strip())
        if key in seen_content and seen_content[key] != rid:
            problems.append(f"{rid}: duplicate of {seen_content[key]}")
        seen_content[key] = rid
        if rec.get("supersedes") and rec["supersedes"] not in state:
            problems.append(f"{rid}: supersedes unknown id")
        if check_sources and rec["verification"] == "verified":
            src = rec["source"]
            m = re.match(r"^commit:([0-9a-f]{7,40})$", src)
            if m:
                if run(["git", "cat-file", "-e", m.group(1) + "^{commit}"], cwd=root)[0] != 0:
                    problems.append(f"{rid}: commit {m.group(1)} not found")
            elif not src.startswith(("user:", "http", "ci:")):
                fp = src.split(":")[0]
                if not (root / fp).exists():
                    problems.append(f"{rid}: source file {fp!r} not found")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root"); ap.add_argument("--project"); ap.add_argument("--check-sources", action="store_true")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    proj = a.project or project_id(root)
    probs = validate(root, proj, a.check_sources)
    print(f"memory[{proj}]: {'OK' if not probs else str(len(probs)) + ' problem(s)'}")
    for p in probs:
        print(" -", p)
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
