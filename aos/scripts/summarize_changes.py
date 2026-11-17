#!/usr/bin/env python3
"""Compact summary of working-tree/branch changes (replaces pasting full diffs into context).

  summarize_changes.py                 # uncommitted changes vs HEAD
  summarize_changes.py --base main     # branch changes vs merge-base with main
"""
from __future__ import annotations

import argparse
import sys

from aos_lib import find_root, run

RISK = [("migration/schema", r"(migrations?/|\.sql$|schema)"), ("auth/permissions", r"(auth|permission|rls|policy|login|token)"),
        ("CI/deploy", r"(\.github/workflows|Dockerfile|deploy|vercel\.json)"), ("secrets/env", r"(\.env|secret|credential|keystore)"),
        ("dependencies", r"(package(-lock)?\.json|build\.gradle|requirements|pyproject|go\.mod|Cargo\.)")]


def summarize(root, base: str | None = None) -> dict:
    import re
    rng = []
    if base:
        rc, mb = run(["git", "merge-base", base, "HEAD"], cwd=root)
        rng = [mb.strip()] if rc == 0 else [base]
    rc, ns = run(["git", "diff", "--numstat", *rng], cwd=root)
    files = []
    for l in ns.splitlines():
        a, d, p = (l.split("\t") + ["", "", ""])[:3]
        files.append({"path": p, "added": int(a) if a.isdigit() else 0, "removed": int(d) if d.isdigit() else 0})
    rc, st = run(["git", "status", "--porcelain"], cwd=root)
    untracked = [l[3:] for l in st.splitlines() if l.startswith("??")]
    tests = [f["path"] for f in files if re.search(r"(test|spec)", f["path"], re.I)]
    risks = sorted({name for f in files + [{"path": u} for u in untracked] for name, rx in RISK if re.search(rx, f["path"], re.I)})
    return {"files": files, "untracked": untracked, "tests_touched": tests, "risk_flags": risks,
            "total_added": sum(f["added"] for f in files), "total_removed": sum(f["removed"] for f in files)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base"); ap.add_argument("--root")
    a = ap.parse_args(argv)
    s = summarize(find_root(a.root), a.base)
    print(f"{len(s['files'])} changed (+{s['total_added']}/-{s['total_removed']}), {len(s['untracked'])} untracked")
    for f in s["files"][:40]:
        print(f"  {f['path']} +{f['added']}/-{f['removed']}")
    for u in s["untracked"][:20]:
        print(f"  ?? {u}")
    print("tests touched:", ", ".join(s["tests_touched"]) or "NONE")
    if s["risk_flags"]:
        print("risk flags:", ", ".join(s["risk_flags"]), "-> needs elevated review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
