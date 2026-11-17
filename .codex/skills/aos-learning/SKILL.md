---
name: aos-learning
description: Capture verified decisions, lessons, failure causes and user preferences into per-project memory with sources; validate and render memory files. Use after a meaningful verified outcome, not after every reply.
---
# aos-learning
**Gate**: a record needs evidence (passing test, commit, doc line, or the user's own statement). AI suggestions/chat imports -> `hypothesis`, `unverified`.
1. Search first: `python3 aos/scripts/memory.py search "<topic>"` (dedupe; supersede instead of duplicating).
2. Add: `memory.py add --category <verified_fact|decision|user_preference|reusable_pattern|verified_solution|confirmed_failure|hypothesis|known_limitation|rejected_approach|pending_decision> --content "<<=600 chars>" --source <path[:line]|commit:<sha>|user:<date>|ci:<run>> --verification verified --confidence high --tags architecture,...`
3. Changed facts: `memory.py supersede <id> ...`; wrong ones: `deprecate`/`rollback` (history is kept).
4. `python3 aos/scripts/validate_memory.py --check-sources` then `memory.py render` (PROJECT_MEMORY, ARCHITECTURE, DECISIONS, LESSONS_LEARNED, KNOWN_FAILURES, TEST_BASELINE, OPEN_TASKS).
5. Chat exports: `discover_history.py import-chat FILE` lists candidates; `--commit` stores them unverified.
**Never store** secrets, tokens, customer/production data. Do not rewrite user preferences without their statement. Policy/gate changes are proposals only. Cross-project (`--project _global`) holds general lessons/preferences only.
