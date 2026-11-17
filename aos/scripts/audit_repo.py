#!/usr/bin/env python3
"""Collect objective facts about a cloned third-party repo for build-vs-reuse audits (no network, no code execution).

  audit_repo.py DIR [--json]
Reports: HEAD commit date, license text id, languages by file count, top-level layout, manifests/runtime deps,
test file count, SKILL.md/AGENTS.md/CLAUDE.md presence, install/lifecycle scripts and risky patterns (grep only).
Facts only: whether a README claim works must still be verified by reading code/tests.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from aos_lib import run

LICENSES = [("Apache-2.0", r"Apache License"), ("MIT", r"Permission is hereby granted, free of charge"),
            ("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC"), ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"),
            ("GPL-2.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 2"), ("LGPL", r"GNU LESSER GENERAL"),
            ("BSD", r"Redistribution and use in source and binary forms"), ("MPL-2.0", r"Mozilla Public License"),
            ("ELv2", r"Elastic License"), ("BUSL", r"Business Source License")]
RISK = {"install_hooks": r"\"(preinstall|postinstall|prepare)\"\s*:", "shell_pipe": r"curl[^\n|]*\|\s*(ba)?sh", "eval_exec": r"\b(eval|exec)\(",
        "subprocess_shell": r"shell\s*=\s*True|child_process", "telemetry": r"(posthog|segment\.io|telemetry|analytics\.track)"}
MANIFESTS = ["package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "setup.py", "pom.xml", "Dockerfile"]


def audit(d: Path) -> dict:
    files = [p for p in d.rglob("*") if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]
    ext = Counter(p.suffix.lower() for p in files)
    rc, date = run(["git", "log", "-1", "--format=%cI %h"], cwd=d)
    lic = next((p for p in d.glob("LICEN[CS]E*")), None)
    lic_id = "none-found"
    if lic:
        t = lic.read_text(errors="replace")[:6000]
        lic_id = next((n for n, rx in LICENSES if re.search(rx, t, re.I)), "unrecognised")
    tests = [p for p in files if re.search(r"(^|/)(tests?|__tests__|spec|e2e)/|\.(test|spec)\.|_test\.", str(p.relative_to(d)))]
    risks: Counter = Counter()
    for p in files[:4000]:
        if p.suffix.lower() in (".py", ".js", ".ts", ".sh", ".json", ".go", ".rs", ".md") and p.stat().st_size < 300_000:
            try:
                t = p.read_text(errors="replace")
            except OSError:
                continue
            for k, rx in RISK.items():
                if re.search(rx, t):
                    risks[k] += 1
    deps = {}
    for m in MANIFESTS:
        for p in list(d.glob(m)) + list(d.glob(f"*/{m}"))[:3]:
            if m == "package.json":
                try:
                    j = json.loads(p.read_text())
                    deps[str(p.relative_to(d))] = {"deps": len(j.get("dependencies", {})), "dev": len(j.get("devDependencies", {})), "scripts": sorted(j.get("scripts", {}))[:8]}
                except (json.JSONDecodeError, OSError):
                    pass
            else:
                deps[str(p.relative_to(d))] = "present"
    return {"dir": d.name, "head": date.strip(), "license_file": lic.name if lic else None, "license": lic_id, "files": len(files),
            "top_exts": ext.most_common(6), "top_level": sorted(p.name for p in d.iterdir() if p.name != ".git")[:40],
            "manifests": deps, "test_files": len(tests), "skill_md": len([p for p in files if p.name == "SKILL.md"]),
            "agent_md": len([p for p in files if p.parent.name == "agents" and p.suffix == ".md"]),
            "instruction_files": sorted(p.name for p in d.iterdir() if p.name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md")),
            "risk_pattern_files": dict(risks)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    if not d.is_dir():
        print("not a directory", file=sys.stderr)
        return 2
    print(json.dumps(audit(d), indent=1 if not a.json else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
