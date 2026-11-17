---
name: aos-repo-scan
description: Index the repository (stack, symbols, tests, CI, AI instruction files) with deterministic tools and find relevant files for a task without loading source into the model. Use before reading unfamiliar code or at project onboarding.
---
# aos-repo-scan
**Input**: optional query ($ARGUMENTS).
1. `python3 aos/scripts/index_repository.py index` - incremental by git blob hash; reports reused vs parsed files.
2. With a query: `python3 aos/scripts/index_repository.py query "<terms>" --limit 8`; exact symbol usages: `... refs <Symbol>`.
3. Onboarding: `python3 aos/scripts/discover_history.py scan` (git history -> candidate failure patterns, saved only with `--commit` as unverified hypotheses). Read existing CLAUDE.md/AGENTS.md/docs before adding anything.
4. Only verified facts go to memory (`aos-learning`). Other repos must be attached/authorised first; never assume a repo is accessible.

**Output**: stack, test/CI inventory, ranked files + symbols + tests. **Safety**: no source is sent to a model by the scripts; index lives in git-ignored `.aos/`.
