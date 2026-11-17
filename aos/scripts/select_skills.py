#!/usr/bin/env python3
"""Skill selector: map a task to the smallest useful set of AOS skills (keyword heuristic, config/skills.yaml).

  select_skills.py "add a migration for the orders table" [--max 3] [--json]
Adds aos-validation for level >= 2 and aos-security-review when security scores >= 2. Recommends only.
"""
from __future__ import annotations
import argparse, json, re, sys
from aos_lib import find_root, load_config
from route_task import route


def select(task: str, max_skills: int = 3, root=None) -> dict:
    reg = (load_config("skills", root) or {}).get("skills") or {}
    hits = []
    for name, spec in reg.items():
        kws = [str(k) for k in (spec or {}).get("keywords") or []]
        n = sum(1 for k in kws if re.search(r"\b" + re.escape(k), task, re.I))
        if n:
            hits.append((n, name))
    hits.sort(key=lambda x: (-x[0], x[1]))
    r = route(task, root=root)
    chosen = [n for _, n in hits[:max_skills]]
    if r["level"] >= 2 and "aos-validation" not in chosen:
        chosen.append("aos-validation")
    if r["scores"].get("security", 0) >= 2 and "aos-security-review" not in chosen:
        chosen.append("aos-security-review")
    return {"skills": chosen or ["aos-optimize"], "level": r["level"], "model": r["model"], "effort": r["effort"],
            "heuristic": True, "matches": {n: c for c, n in hits}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task"); ap.add_argument("--max", type=int, default=3); ap.add_argument("--root"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = select(a.task, a.max, find_root(a.root))
    print(json.dumps(r, indent=2) if a.json else f"skills: {', '.join(r['skills'])} | level {r['level']} | model {r['model']} ({r['effort']}) [heuristic]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
