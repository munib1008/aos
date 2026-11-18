# AOS - Adaptive Optimization System

Provider-independent skill + agent framework for Claude Code and Codex: complexity routing, minimal-context building, verified per-project memory, safe session fork/handoff, honest test summaries, video assembly with local FFmpeg, specialist skills (API, database, UI/UX, automation, research, AI/ML, security review, humanizer) and a **Token Capacity & Compute Manager (TCCM)** for budget-aware model selection. Python standard library only (3.9+).

## Download and use (`dist/`)
Pick one form. Two profiles: **default** (full, generic) and **dormakaba** (policy-restricted; no TCCM, AI Platform catalog).

| File | Use as | Install |
|---|---|---|
| `aos-<profile>-skill.zip` | one Agent Skill with scripts | unzip so the folder is `~/.claude/skills/aos/` (Claude Code) or `~/.codex/skills/aos/` (Codex) |
| `aos-<profile>-skill-noscript.zip` | claude.ai skill upload (no code allowed; written procedures) | Settings > Capabilities > Skills > upload |
| `aos-<profile>-claude-code.zip` | the master agent + master skill + `/aos` | unzip at the project root; delegation: `claude --agent orchestrator` |
| `aos-<profile>-codex.zip` | the master agent (TOML) + master skill | unzip at the project root |

Check files: `cd dist && sha256sum -c SHA256SUMS`. From a clone: `python3 aos/install.py --target <project> --harness both [--profile dormakaba]` or `--global`. Rebuild: `python3 aos/packaging/package.py build --out dist && python3 aos/packaging/package.py verify dist`.

## One master agent
**Everything is combined into one agent (`aos`), one skill (`aos`) and one command (`/aos`).** The skill loads its 21 modules (specialist skills, role details, Project OMEGA spec, capacity routing) only when needed, so the always-listed context shrinks from 43 entries / 7,516 description characters to 3 entries / 904 (about -88%, measured by character count; tokens estimated).
Call it anywhere: `claude --agent aos "<task or goal>"`, `/aos <task>`, or unattended `omega_runner.py run --goal "..."` (waits out provider usage limits, resumes, never bypasses them). Install once with `python3 aos/install.py --global --harness both`; older multi-agent installs are migrated automatically. Details and honest limits: `aos/docs/OMEGA.md`.

## What is in it
| Area | Components |
|---|---|
| Core | router (8 dimensions, levels 1-4, tier floors), `build_context` (priority tiers, budget), `index_repository`, `memory` (provenance, isolation, secret refusal), `summarize_tests/changes`, `session_manager`, `create_handoff` (redacted) |
| Skills (21 default / 19 dormakaba) | 8 core, `aos-video`, `aos-omega`, 7 specialist, `humanizer`, `aos-token-capacity`, `aos-local-inference`, `aos-model-routing` (default only), `model-advisor` (dormakaba only) |
| TCCM (default) | `tccm.py`: hardware inventory + local-model sizing, OpenAI-compatible local adapter, model selector (modes `local_first/balanced/quality_first`, budget gate, privacy/terms/tier filters, evidence-based escalation), usage ledger + dashboard (paid / subscription / free-tier / local / credits never mixed), safe exact-match cache + duplicate-call detection, model-quality benchmark, measured-only capacity plan, read-only credits registry, usage-limit handoff |
| Packaging | 8 reproducible zips, profile overlays, `package.py verify` |

## Verified vs not (honest status)
| Claim | Status |
|---|---|
| 107 unit tests (router, memory, index, context, sessions, handoff, video, Codex export, TCCM with a mock OpenAI endpoint, profiles, packaging, dormakaba suite on the built profile) | pass |
| All 8 zips unzip, validate against the Agent Skills rules, run a script smoke test, contain no secrets, no profile leakage | pass (`package.py verify`) |
| Context loading vs grep+read: 0.01-0.24x tokens, higher recall on 5 repos (`aos/docs/BENCHMARKS.md`) | measured, **proxy only** |
| OMEGA ledger, evidence gate, guard, limit-wait supervisor (29 tests incl. a real child process that waits out a simulated limit); `omega` agent loads in the installed Claude Code | pass / verified live (agent load only) |
| Guard hook firing in live Claude Code, real provider limit, Codex preset, unattended run on a real repo | **not verified** |
| Real local / low-cost / strong model comparison, cost per successful task | **not run**: no GPU, engine or model access in the build sandbox; harness + one-command runner ready (`aos/docs/RUN_BENCHMARK.md`) |
| Live loading in Claude Code, Codex, claude.ai | **not verified** here (files are spec-valid) |
| End-to-end cost savings | **unmeasured** |
| Priors for model success rates | **uncalibrated assumptions**, flagged in output; replaced by measured stats at 5+ samples |

## Safety stance
No quota evasion, fake referrals, credit farming, unofficial proxies or auth bypass. AOS recommends and accounts; it never proxies proprietary-provider traffic, never stores API keys, and authorizes no paid-API spend by default. Details: `aos/docs/TCCM_AUDIT.md`, `aos/docs/BUILD_VS_REUSE.md`, `aos/docs/REUSE_LOG.md`, `aos/docs/COMPATIBILITY.md`, `docs/PROFILES.md`, `docs/MERGE_REPORT.md`.
