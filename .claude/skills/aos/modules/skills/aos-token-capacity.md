<!-- aos-token-capacity: Inventory and plan AI compute capacity - hardware, local engines, configured providers, budgets, usage dashboard, cache stats, official-credit registry and a measured-only capacity plan. Use when aske -->
# aos-token-capacity (TCCM)
**Principle**: legitimate capacity only - local compute, configured providers, caching, less wasted context. Paid tokens, subscription allowance, free-tier quota, local tokens and credits are **different quantities and are never added together**.

## Steps
1. **Inventory**: `python3 aos/scripts/tccm.py inventory` (RAM, CPU, GPU/VRAM, disk, acceleration, running local engines, enabled providers, local-model size recommendation).
2. **Record usage** as work happens: `tccm.py budget record --kind paid_api|subscription|free_tier|local|credits --provider P --model M --task T --input-tokens N --output-tokens N [--cached-tokens N] [--cost-actual USD|--cost-estimated USD] --success true|false --category C`. Unknown price stays *unknown*, never 0.
3. **Budgets**: `tccm.py budget status`. Default authorizes **no** paid-API spend (`config/budgets.yaml`); over-ceiling or unpriced paid calls return `needs_authorization`. Only the human runs `budget authorize`.
4. **Dashboard**: `tccm.py dashboard --html` -> `.aos/dashboard.html` (separate sections per kind, measured vs estimated cost, retries, success rate, escalations, local RAM/GPU).
5. **Plan**: `tccm.py plan` - each lever says `measured` or `no_data`; no aggregate saving is claimed without measurements.
6. **Credits/programs** (read-only): `tccm.py credits` lists entries you registered from OFFICIAL pages with expiry/staleness. Never apply, refer, farm or claim; a credit counts only when `status: confirmed` with a provider reference.

## Output contract
Table of capacity by kind, spend vs ceilings, what is measured vs estimated, and the next data needed. Cite `evidence` fields.

## Safety
No quota evasion, fake referrals, account farming, CAPTCHA bypass or unofficial proxies. Never store keys (providers.yaml names env vars only). Free-tier terms must be verified and dated before routing uses them.
