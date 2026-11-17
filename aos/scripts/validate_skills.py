#!/usr/bin/env python3
"""Validate skills against the open Agent Skills format (agentskills.io specification) plus Codex size limits.

  validate_skills.py [DIR ...]      # default: aos/skills
Rules (from the spec's frontmatter table): name 1-64 chars [a-z0-9-], no leading/trailing/double hyphen, equals
directory name; description 1-1024; compatibility <=500; metadata is a string map. Extra check: injected body <= 8192 B
(Codex limit as reported by a third-party adapter - treated as a conservative cap, not verified against OpenAI docs).
Independent re-implementation (the reference `skills-ref` needs Python>=3.11 + click + strictyaml; AOS stays dependency-free).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from aos_lib import AOS_DIR, parse_yaml

CODEX_CAP = 8192


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise ValueError("unterminated frontmatter")
    out = {}
    for line in head.splitlines():          # description values may contain ': ' so avoid full YAML here
        if re.match(r"^[A-Za-z_-]+:\s", line) or re.match(r"^[A-Za-z_-]+:$", line):
            k, _, v = line.partition(":")
            v = v.strip()
            out[k] = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'" else v
    if "metadata:" in head:
        out["metadata"] = parse_yaml(head).get("metadata") or {}
    return out, body


def validate_skill(skill_md: Path) -> list[str]:
    errs = []
    try:
        fm, body = split_frontmatter(skill_md.read_text(encoding="utf-8"))
    except ValueError as e:
        return [f"{skill_md}: {e}"]
    name, desc = fm.get("name", ""), fm.get("description", "")
    if not (1 <= len(name) <= 64) or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name or "-"):
        errs.append(f"name {name!r} invalid (lowercase a-z0-9, single hyphens, 1-64)")
    if name != skill_md.parent.name:
        errs.append(f"name {name!r} must equal directory {skill_md.parent.name!r}")
    if not 1 <= len(desc) <= 1024:
        errs.append(f"description length {len(desc)} not in 1..1024")
    if len(fm.get("compatibility", "")) > 500:
        errs.append("compatibility > 500 chars")
    if "metadata" in fm and not isinstance(fm["metadata"], dict):
        errs.append("metadata must be a mapping")
    if len(body.encode()) > CODEX_CAP:
        errs.append(f"body {len(body.encode())} B exceeds Codex cap {CODEX_CAP} B")
    return [f"{skill_md.parent.name}: {e}" for e in errs]


def validate_dir(d: Path) -> list[str]:
    files = sorted(d.glob("*/SKILL.md"))
    if not files:
        return [f"{d}: no skills found"]
    return [e for f in files for e in validate_skill(f)]


def main(argv=None) -> int:
    dirs = [Path(a) for a in (argv if argv is not None else sys.argv[1:])] or [AOS_DIR / "skills"]
    errs = [e for d in dirs for e in validate_dir(d)]
    print("skills: OK" if not errs else f"skills: {len(errs)} problem(s)")
    for e in errs:
        print(" -", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
