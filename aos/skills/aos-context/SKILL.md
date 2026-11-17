---
name: aos-context
description: Build or trim a minimal task context bundle with priority tiers P0-P6, compress stale context and large tool output, and preserve critical constraints. Use when context grows large or before delegating to a subagent.
---
# aos-context
1. Build: `python3 aos/scripts/build_context.py "<task>" --criteria "<criteria>" [--level N] [--background]`.
2. Tiers: P0 task+criteria, P1 safety/invariants, P2 affected code snippets, P3 architecture, P4 decisions, P5 lessons/failures, P6 background (opt-in). P0-P3 always load; the rest only inside the token budget (estimate, chars/4).
3. Tool output: large logs go to `.aos/artifacts/`; keep command, exit code, counts, failing ids, short excerpts, path (`summarize_tests.py`, `summarize_changes.py`).
4. Never drop: error text needed to diagnose, hard requirements, accepted decisions, security constraints.
**Output**: the bundle + `omitted` list. If P2 is empty, refine the query rather than reading broadly.
