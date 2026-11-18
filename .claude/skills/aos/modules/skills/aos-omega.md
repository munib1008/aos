<!-- aos-omega: Project OMEGA - take any existing repository from audit to release-ready - evidence-gated backlog, dependency-ordered completion of end-to-end workflows, security/data/UX/performance hardening, 100-po -->
# aos-omega
**Contract**: audit what truly exists -> complete critical end-to-end workflows -> stabilize -> redesign -> optimize -> validate. Every claim needs evidence; nothing is "done" because a page or route exists. Full spec (on demand): `references/MASTER_PROMPT.md`; shortened follow-up prompt: `references/CONTINUE_PROMPT.md`; domain packs: `references/domain-*.md` if present.

## Tools (state in `.aos/omega/`, docs in `docs/`)
- `python3 aos/scripts/omega.py init|status|next|backlog|feature|accept|score|gate|done|approvals|guard|checkpoint|report` (see `--help`).
- Evidence formats: `test:<cmd> => PASSED`, `file:<path[:line]>`, `commit:<sha>`, `doc:<path>`, `url:https://...`, `run:<cmd> => exit 0`. Fabricated or stale evidence is rejected and re-verified by the gate.
- Unattended multi-cycle runs with limit waiting: `python3 aos/scripts/omega_runner.py run --goal "..." [--repo URL]`.

## Method
1. **Audit** (no assumptions): structure, docs, git, manifests, routes, auth, schema/migrations, integrations, tests, CI, deployment, observability. Use `index_repository.py`, search first, read ranges. Build the feature matrix (`omega.py feature add`: COMPLETE_VERIFIED / COMPLETE_UNVERIFIED / PARTIAL / BROKEN / MOCK_ONLY / MISSING / DEPRECATED / BLOCKED) and reconstruct user journeys start-to-closure.
2. **Backlog**: one item per gap (20 categories), severity, factors 0-3, dependencies. Priority is a transparent weighted score (`config/omega.yaml`); critical security/data-integrity items are P0. Order: authn -> authz -> data -> workflows -> UI -> E2E tests. Shared foundations before modules.
3. **Complete** vertical slices with loading/empty/error/permission/retry/concurrency states, state machines, audit events, transactions, idempotency. Backend enforces every permission.
4. **Verify** with real tests (unit, integration, API contract, authz, E2E, a11y, migration, resilience). Never report an unrun test as passed; capture invariants before changing behavior.
5. **Redesign/optimize** only with a measured or defensible benefit: design tokens + shared components, accessibility, performance budgets, profiling before/after.
6. **Close**: `omega.py score set` per category from evidence, `omega.py gate`, `omega.py report`. Release readiness is exactly the gate's level; production needs a human-approved `production_release`.

## Autonomy
Routine reversible work proceeds without approval. Guarded actions (destructive, deploy/publish, non-local migrations, cloud/IAM, secrets, policy changes) are queued by `omega.py guard` for a human while work continues elsewhere. Usage limits: checkpoint, wait for the reset (never bypass), resume from the ledger.

## Output contract
Per milestone: Baseline, Problems, Changes, Design, Validation, Completion status (ledger counts), Pending approvals, Next actions, Release readiness. No invented scores, results or integrations.
