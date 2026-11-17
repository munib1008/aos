---
name: aos-session-fork
description: Create, fork, resume, and archive portable AOS session manifests; decide when to compact or fork; isolate independent implementation in a git worktree. Use for the aos-fork and the aos-resume.
---
# aos-session-fork
- **Health**: `python3 aos/scripts/session_manager.py health --window-frac F --large-outputs N --failures N [--topic-change] [--milestone]` -> continue | consider-compact | compact | fork-analysis | new-session.
- **Save before compact/fork**: `session_manager.py update <id> --done .. --pending .. --decision .. --file .. [--tests-json "$(summarize_tests.py --json parse ...)"]`.
- **Fork** (only for independent work / alternatives / noisy investigation, never just because a task is big): `session_manager.py fork <parent|latest-id> "<task>" --kind analysis|implementation --files a,b [--worktree]`. Overlapping planned files with another active implementation session are refused.
- **Resume**: `session_manager.py resume [id|latest]` prints the brief and WARNs on branch/commit drift, unrecorded dirty files, non-passing tests. Re-verify before trusting.
- **Merge**: `session_manager.py merge-check <id>` -> human review. Never auto-merge. **Archive**: `archive <id>`.
A chat fork is not a git branch: implementation forks get their own `aos/<id>` branch/worktree.
