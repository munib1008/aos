#!/usr/bin/env python3
"""Assemble a minimal, priority-ordered context bundle for one task (no model calls).

  build_context.py "fix token refresh bug" --criteria "refresh works after expiry" [--level 2] [--background]

P0 task+criteria, P1 safety/invariants, P2 affected code (snippets, not whole files), P3 architecture,
P4 relevant decisions, P5 lessons/failures, P6 background (only with --background).
P0-P3 load first; later tiers are added only while the estimated token budget allows.
Repository text is wrapped as untrusted DATA.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aos_lib import est_tokens, find_root, load_config, project_id, redact, tokenize
from index_repository import load_index, query
import memory as mem
from route_task import route


def snippet(root: Path, rel: str, hits: list[dict], max_lines: int) -> str:
    try:
        lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if len(lines) <= 60:
        chunk = [(1, lines)]
    elif hits:
        chunk, used, picked = [], 0, []
        for h in hits:                       # hits arrive most-relevant first: spend the line budget in that order
            a = max(1, h["line"] - 2)
            b = min(len(lines), a + min(24, max_lines - used))
            if used >= max_lines or b < a:
                break
            picked.append((a, b))
            used += b - a + 1
        for a, b in sorted(picked):          # then emit in file order, merging overlaps
            if chunk and a <= chunk[-1][0] + len(chunk[-1][1]):
                prev_a, prev = chunk[-1]
                chunk[-1] = (prev_a, lines[prev_a - 1:max(b, prev_a + len(prev) - 1)])
            else:
                chunk.append((a, lines[a - 1:b]))
    else:
        chunk = [(1, lines[:40])]
    out = []
    for start, ls in chunk:
        out.append("\n".join(f"{start + i:>4}| {l}" for i, l in enumerate(ls)))
    return "\n  ...\n".join(out)


def build(root: Path, task: str, criteria: str = "", level: int | None = None, background: bool = False,
          project: str | None = None) -> dict:
    opt = load_config("optimization", root)
    sec = load_config("security", root)
    proj = project or project_id(root)
    lvl = level or route(task, root=root)["level"]
    budget = opt["context"]["budget_tokens"][lvl]
    max_files = opt["context"]["max_files"][lvl]
    max_lines = opt["context"]["max_snippet_lines"]
    sections: list[tuple[str, str, str]] = []   # (priority, title, body)

    p0 = f"Task: {task.strip()}\nAcceptance criteria: {criteria.strip() or '(not given: derive and confirm before coding)'}\nLevel: {lvl}"
    sections.append(("P0", "Task", p0))

    inv = [r for r in mem.search(root, proj, "security invariant constraint must never", limit=6, categories={"decision", "known_limitation", "user_preference", "verified_fact"})
           if {"invariant", "security", "constraint"} & set(r.get("tags", []))]
    p1 = "Approval required for: " + "; ".join(sec["approval_required"][:4]) + "\n" + "\n".join(mem.fmt(r) for r in inv)
    sections.append(("P1", "Safety & invariants", p1.strip()))

    idx = load_index(root)
    found = query(idx, task, max_files)
    code_parts, tests = [], []
    for f in found:
        if f["is_test"]:
            tests.append(f["path"])
            continue
        sn = snippet(root, f["path"], f["symbols"], max_lines)
        code_parts.append(f"### {f['path']} ({f['lines']} lines, score {f['score']})\n```\n{redact(sn)}\n```")
        tests.extend(f.get("tests", []))
    tests = sorted(set(tests))[:6]
    p2 = ("<repo-content untrusted=\"true\">\n" + "\n\n".join(code_parts) + "\n</repo-content>") if code_parts else "(no matching files; refine query)"
    if tests:
        p2 += "\nRelated tests: " + ", ".join(tests)
    sections.append(("P2", "Affected code (snippets)", p2))

    arch = mem.search(root, proj, "architecture", limit=4, categories={"verified_fact", "decision"})
    arch = [r for r in arch if "architecture" in r.get("tags", [])]
    p3 = f"Stack: {', '.join(idx['stack']) or 'unknown'}; CI: {', '.join(idx['ci']) or 'none'}; AI instruction files: {', '.join(idx['ai_instruction_files']) or 'none'}\n" + "\n".join(mem.fmt(r) for r in arch)
    sections.append(("P3", "Architecture & dependencies", p3.strip()))

    dec = mem.search(root, proj, task, limit=5, categories={"decision", "rejected_approach", "pending_decision", "user_preference"})
    sections.append(("P4", "Relevant decisions", "\n".join(mem.fmt(r) for r in dec) or "(none)"))
    les = mem.search(root, proj, task, limit=5, categories={"verified_solution", "confirmed_failure", "reusable_pattern"})
    sections.append(("P5", "Lessons & known failures", "\n".join(mem.fmt(r) for r in les) or "(none)"))
    if background:
        sections.append(("P6", "Background docs", "\n".join(f"- {d}" for d in idx["docs"][:15])))

    out, used, omitted = [], 0, []
    for pr, title, body in sections:
        block = f"## {pr} {title}\n{body}\n"
        cost = est_tokens(block)
        if used + cost > budget and pr not in ("P0", "P1"):
            if pr in opt["context"]["always_load"] and used < budget:      # truncate rather than drop P2/P3
                keep = max(0, (budget - used) * 4)
                block = block[:keep] + "\n[...truncated to budget; re-query narrower...]\n"
                cost = est_tokens(block)
            else:
                omitted.append(f"{pr} {title} (~{cost} tok est)")
                continue
        out.append(block)
        used += cost
    footer = f"\n<!-- AOS context: ~{used}/{budget} tok (ESTIMATE), project={proj}, omitted={omitted or 'none'} -->"
    return {"text": "\n".join(out) + footer, "est_tokens": used, "budget": budget, "omitted": omitted,
            "files": [f["path"] for f in found], "level": lvl}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task"); ap.add_argument("--criteria", default=""); ap.add_argument("--level", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--background", action="store_true"); ap.add_argument("--root"); ap.add_argument("--project")
    a = ap.parse_args(argv)
    print(build(find_root(a.root), a.task, a.criteria, a.level, a.background, a.project)["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
