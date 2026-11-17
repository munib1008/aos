# AOS - Adaptive Optimization System

Lightweight, provider-independent agent + skill package for Claude Code that cuts avoidable context and rework in AI-assisted development **without trading away correctness, security or tests**. Python standard library only (3.9+); no network calls, no model calls inside the scripts.

## What is real vs. not
| Component | State |
|---|---|
| Router (complexity 8x0-3, levels 1-4, model/effort, escalation) | Implemented, unit-tested. Scores are **keyword heuristics**; thresholds are **initial, uncalibrated**. |
| Per-project memory (JSONL, sources, verification, supersede/rollback, isolation, secret refusal) | Implemented, tested |
| Repo index / query / refs (incremental by git blob hash) | Implemented, tested |
| Context builder (P0-P6, token budget **estimate** = chars/4) | Implemented, tested |
| Test/diff summarizers (full logs to `.aos/artifacts`, honest status) | Implemented, tested |
| Session manifests, fork (worktree), resume drift checks, health policy | Implemented, tested |
| Handoff export (redacted, aborts on surviving secret) | Implemented, tested. **No sync with ChatGPT/Codex exists**; you hand over the folder. |
| Usage measurement + baseline-vs-AOS compare | Parser tested on a **synthetic** transcript; not yet validated on a live Claude Code transcript. Prices are null (not invented). |
| Token/cost savings, 20-40% targets | **Unmeasured.** Targets only; run `docs/BENCHMARKS.md`. |
| Agents/skills/commands loading in a live Claude Code session | Files are well-formed (tested); **live invocation not yet verified** in this environment. |
| Project history and scoping | **Deferred to Phase 2** (`aos/docs/ROADMAP.md`); AOS ships generic with empty memory and registry. |

## Install
```bash
python3 aos/install.py --target /path/to/your/project      # copies .claude/{agents,skills,commands} + runtime to .claude/aos
python3 aos/install.py --target /path/to/your/project --uninstall
python3 -m unittest discover -s aos/tests                   # self-test (38 tests)
```
Idempotent; appends a marked block to the project's `AGENTS.md`/`CLAUDE.md` (never overwrites), adds `.aos/` to `.gitignore`.

## Use
`/aos-optimize` `/aos-scan` `/aos-route` `/aos-learn` `/aos-fork` `/aos-resume` `/aos-handoff` `/aos-audit` (commands -> skills). For delegation run `claude --agent orchestrator` (subagents cannot spawn subagents). Scripts also work standalone: `python3 aos/scripts/<name>.py --help`.

## Onboarding a project (per repo, authorised access only)
1. `index_repository.py index` then `discover_history.py scan` (local git history -> *unverified* candidates).
2. Read the project's docs/CLAUDE.md; record only source-backed facts: `memory.py add ... --source file:line --verification verified`.
3. CI failures/PRs/issues: fetch with your GitHub tooling, then record confirmed causes. Chat history: export to md/json/txt, `discover_history.py import-chat FILE` (kept as hypotheses until you verify).
4. `validate_memory.py --check-sources`, `memory.py render`.
Edit `aos/config/models.yaml` `available` to what your plan really offers; fill `price_per_mtok` to get cost figures.

Design rationale, limits and risks: `aos/docs/DESIGN.md`. Measurement protocol: `aos/docs/BENCHMARKS.md`.
