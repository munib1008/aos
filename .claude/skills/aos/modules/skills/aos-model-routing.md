<!-- aos-model-routing: Choose the most economical reliable model for a task across local, free-tier, subscription and paid options under a budget - task category, complexity level, eligibility (privacy, terms, size, tier fl -->
# aos-model-routing
**Run**: `python3 aos/scripts/tccm.py route "<task>" [--mode local_first|balanced|quality_first] [--confidential] [--input-tokens N]`.

## What it decides
`decision`: `deterministic` (use the named script, no LLM) | `execute` | `needs_authorization` | `no_candidate`.
Order: deterministic tools -> eligible local model (only low-risk, p_success >= threshold) -> verified free tier -> subscription/credits -> paid API; critical work (level 4, security) has a tier floor and requires independent verification + human approval for deployment.

## Rules you must follow
- Hard filters: provider enabled; confidential tasks only to local or verified `no_training` providers; free tier needs `terms_verified_on` within 90 days and commercial-use confirmation; level floors by tier; context fits.
- p_success is *measured* (>=5 ledger samples) else a labelled **prior**. Say which. Costs use configured prices; unknown price = unknown.
- Modes: `local_first` prefers capable local; `balanced` minimizes expected cost per success (+ latency/failure penalties, ranking only); `quality_first` maximizes success for level>=3.
- `needs_authorization`: stop and ask the human; offer `alternative_without_authorization`. Never raise ceilings yourself.
- Escalation (`escalation_path`): only after a failure that adds NEW evidence; identical retries are refused. After repeated failures or a usage limit: `tccm.py switch --provider P --reason "..."` writes a handoff + `SWITCH_REQUEST.md`; the user authorizes any cost change. AOS never switches providers, bypasses auth, caps or restrictions.
- The harness (Claude Code / Codex) executes cloud calls with its own auth; AOS never proxies proprietary requests.

## Output contract
Decision, chosen model + why (p_success source, est. cost), excluded candidates with reasons, verification required, escalation path.
