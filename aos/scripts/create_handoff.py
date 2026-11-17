#!/usr/bin/env python3
"""Export a provider-neutral handoff package (Claude -> ChatGPT/Codex or any coding agent).

  create_handoff.py [--session ID|latest] [--out DIR]

Writes HANDOFF.md, TASK_CONTEXT.json, ARCHITECTURE_SUMMARY.md, CHANGES.md, TEST_RESULTS.md, NEXT_ACTIONS.md.
All text is redacted; the export aborts if a secret pattern survives. Memory comes from THIS project only.
Nothing is synced to any provider: the user must hand the folder over (and verify access) themselves.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aos_lib import atomic_write, contains_secret, find_root, git_state, load_config, now_iso, project_id, redact
from index_repository import load_index
import memory as mem
import session_manager as sm
from summarize_changes import summarize

PROMPT = ("Continue development using the attached AOS handoff. Inspect the recorded Git state, verify completed work, "
          "preserve project decisions, solve remaining issues and run relevant tests. Do not restart completed work or "
          "assume unverified claims are correct.")


def build(root: Path, sid: str | None, out: Path | None = None) -> Path:
    proj = project_id(root)
    try:
        m = sm.load(root, sid or "latest")
    except SystemExit:
        m = None
    g = git_state(root)
    idx = load_index(root)
    ch = summarize(root, None)
    recs = [r for r in mem.load_state(root, proj).values() if r.get("status", "active") == "active"]
    keep = [r for r in recs if r["category"] in ("decision", "rejected_approach", "known_limitation", "confirmed_failure", "pending_decision", "user_preference")
            or "architecture" in r.get("tags", [])][:40]
    tests = (m or {}).get("tests", [])
    ctx = {
        "aos_handoff_version": 1, "created": now_iso(), "project": proj, "repository": g["remote"], "branch": g["branch"], "commit": g["commit"],
        "dirty_files": g["dirty_files"], "session_id": (m or {}).get("session_id"), "parent_session": (m or {}).get("parent_session"),
        "objective": (m or {}).get("objective", "(no session manifest: state the objective explicitly)"),
        "requirements": (m or {}).get("requirements", []), "decisions": (m or {}).get("decisions", []),
        "completed": (m or {}).get("completed", []), "pending": (m or {}).get("pending", []), "blockers": (m or {}).get("blockers", []),
        "files_modified": sorted(set((m or {}).get("files_modified", [])) | set(g["dirty_files"])),
        "tests": tests, "stack": idx["stack"], "ci": idx["ci"], "test_files": len(idx["tests"]),
        "memory_records": [{k: r[k] for k in ("id", "category", "content", "source", "verification", "confidence")} for r in keep],
        "environment_requirements": "Secrets are NOT included. Re-create env vars/credentials yourself; see repo docs.",
        "continuation_prompt": PROMPT,
    }
    failing = [t for t in tests if t.get("status") != "PASSED"]
    files = {
        "HANDOFF.md": f"""# AOS Handoff - {proj}
Created {ctx['created']}. Source repo: {ctx['repository'] or '(unknown)'} | branch `{g['branch']}` | commit `{g['commit'][:12]}`

## Continuation prompt
> {PROMPT}

## Objective
{ctx['objective']}

## Requirements
{chr(10).join('- ' + r for r in ctx['requirements']) or '- (none recorded)'}

## Decisions & constraints (verify against source before relying)
{chr(10).join('- [' + r['category'] + '/' + r['verification'] + '] ' + r['content'] + ' (src: ' + r['source'] + ')' for r in ctx['memory_records']) or '- (none)'}
{chr(10).join('- ' + d for d in ctx['decisions'])}

## Status
Completed: {'; '.join(ctx['completed']) or 'nothing recorded'}
Pending: {'; '.join(ctx['pending']) or 'nothing recorded'}
Blockers: {'; '.join(ctx['blockers']) or 'none'}
Failing/non-passing test runs: {len(failing)}

## Validate before continuing
1. Confirm you can access the repo and check out `{g['branch']}`; confirm `git rev-parse HEAD` vs `{g['commit'][:12]}`.
2. Compare `git status` with CHANGES.md; uncommitted work may be lost if this was not committed/pushed.
3. Re-run the tests in TEST_RESULTS.md; do not trust recorded results.
4. Check migrations and environment/tool availability. Resolve conflicting instructions (AGENTS.md vs this file) with the user.
""",
        "ARCHITECTURE_SUMMARY.md": f"""# Architecture summary - {proj}
Stack (detected from manifests): {', '.join(idx['stack']) or 'unknown'}
CI: {', '.join(idx['ci']) or 'none'} | Test files: {len(idx['tests'])} | Indexed files: {idx['stats']['files']}
Existing AI instruction files: {', '.join(idx['ai_instruction_files']) or 'none'}

## Architecture records
{chr(10).join('- ' + r['content'] + ' (src: ' + r['source'] + ')' for r in recs if 'architecture' in r.get('tags', [])) or '- none recorded; inspect docs/ and the index'}
""",
        "CHANGES.md": "# Changes (uncommitted, vs HEAD)\n" + "\n".join(f"- {f['path']} +{f['added']}/-{f['removed']}" for f in ch["files"])
                      + "\n" + "\n".join(f"- {u} (untracked)" for u in ch["untracked"]) + f"\n\nRisk flags: {', '.join(ch['risk_flags']) or 'none'}\nFiles recorded by session: {', '.join(ctx['files_modified']) or 'none'}\n",
        "TEST_RESULTS.md": "# Test results (recorded, UNVERIFIED by the receiver)\n" + ("\n".join(
            f"- **{t.get('status')}** `{t.get('command')}` total={t.get('total')} failed={t.get('failed')} failing={t.get('failure_ids')} log={t.get('full_log')}" for t in tests) or "- no test runs recorded") + "\n",
        "NEXT_ACTIONS.md": "# Next actions\n" + ("\n".join(f"{i}. {p}" for i, p in enumerate(ctx["pending"], 1)) or "1. Define remaining work from the objective") +
                           ("\n\nFix first (non-passing tests):\n" + "\n".join(f"- {t.get('command')}: {t.get('failure_ids')}" for t in failing) if failing else "") + "\n",
        "TASK_CONTEXT.json": json.dumps(ctx, indent=2),
    }
    out = out or (root / ".aos" / "handoffs" / (ctx["session_id"] or "adhoc"))
    for name, text in files.items():
        text = redact(text)
        if contains_secret(text):
            raise SystemExit(f"abort: secret pattern remained in {name}")
        atomic_write(out / name, text)
    if m:
        m["handoff_status"] = "exported"
        sm.save(root, m)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session"); ap.add_argument("--out"); ap.add_argument("--root")
    a = ap.parse_args(argv)
    out = build(find_root(a.root), a.session, Path(a.out) if a.out else None)
    print(out)
    for p in sorted(out.iterdir()):
        print(" ", p.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
