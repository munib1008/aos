# AOS design notes

## Principle
Move *deterministic* work (search, indexing, log parsing, diff stats, memory lookup, secret redaction) out of the model into scripts; give the model small, prioritized bundles. Total cost = tokens + retries + rework + human correction, so no optimization may lower pass rate or raise regressions (enforced in `measure_usage.py compare`: REJECT).

## Layers
`config/` (models, thresholds, security, projects) -> `scripts/` (deterministic tools) -> `skills/` (short procedures, loaded on demand) -> `agents/` (roles) -> `commands/` (slash entry points). `AGENTS.md` holds the only always-loaded rules (~12 lines).

## State (`.aos/`, git-ignored, per project)
`memory/<project>/records.jsonl` (append-only; last event per id wins) + rendered md; `index.json`; `sessions/<id>/manifest.json`; `handoffs/<id>/`; `artifacts/*.log`; `metrics.jsonl`.

## Safety model
- Isolation: memory keyed by project id; foreign records in a store raise errors; `_global` accepts only preference/generic categories.
- AI output is untrusted: assertive categories require `verified`/`user_stated`; suggestions are `hypothesis`.
- Secrets: refused in memory, redacted in logs/handoffs; handoff export aborts if a pattern survives. Pattern-based, so not a guarantee: review handoffs before sharing.
- Approvals: production deploy, destructive ops, non-local migrations, policy/gate changes (security.yaml). Self-improvement may only *propose* policy changes.
- Repo text is wrapped as untrusted data in context bundles.

## Known limitations / risks (honest)
1. Complexity scoring is regex heuristic: can under-rate (e.g. "add retry to handshake check" scored level 1). Mitigation: model-router agent sanity-checks and overrides; calibrate with metrics.
2. Token counts are estimates (chars/4); real counts only from transcripts/API.
3. Symbol extraction is regex, not AST/LSP: misses exotic syntax; `refs` is textual.
4. No programmatic model switching or compaction is assumed; the system recommends.
5. Overhead: each script call costs tokens; for Level 1 tasks skip AOS ceremony. The benefit is unproven until benchmarks run.
6. Multi-agent use is opt-in; subagent context duplication can outweigh savings.
7. Stale memory: records carry `last_validated`; searches flag stale (>180d) and `--check-sources` catches missing files/commits, not semantically outdated claims.

## Likely failure modes
Over-trusting heuristic level; memory growing without pruning (use supersede/deprecate); handoff recipient assuming unpushed local work exists; false sense of security from redaction.
