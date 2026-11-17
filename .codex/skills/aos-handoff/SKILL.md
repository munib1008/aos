---
name: aos-handoff
description: Export a provider-neutral handoff package (HANDOFF.md, TASK_CONTEXT.json, ARCHITECTURE_SUMMARY.md, CHANGES.md, TEST_RESULTS.md, NEXT_ACTIONS.md) so ChatGPT/Codex or another agent can continue. Use when switching provider or limits block progress.
---
# aos-handoff
1. Persist current state: `session_manager.py update ...` (completed, pending, decisions, tests).
2. `python3 aos/scripts/create_handoff.py [--session ID]` -> `.aos/handoffs/<id>/`. Output is redacted and the export aborts if a secret pattern survives.
3. Read HANDOFF.md once; confirm it is accurate. Commit/push or otherwise preserve uncommitted work yourself if the receiver needs it (ask the user).
4. Give the user the folder path and the continuation prompt. Receiver must validate: repo access, branch, `git rev-parse HEAD`, `git status`, migrations, tool availability, rerun tests.
Switch conditions: model/limit blocked, repeated evidence-based failure, benchmark evidence, or user request - not one failed test. No synchronization with ChatGPT exists; do not claim one.
