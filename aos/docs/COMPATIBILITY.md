# Claude Code and Codex compatibility
| Concern | Claude Code | Codex | AOS mechanism | Status |
|---|---|---|---|---|
| Shared instructions | `CLAUDE.md` (imports `@AGENTS.md`) | `AGENTS.md` | one `AGENTS.md`, marked block via `install.py` (idempotent) | generated, tested |
| Skills | `.claude/skills/<n>/SKILL.md` | `.codex/skills/<n>/SKILL.md` | single source `aos/skills`; `install.py --harness claude|codex|both` | files validated against spec rules; **live load unverified** |
| Subagents | `.claude/agents/*.md` (tools, model) | `.codex/agents/*.toml` (name, description, model, sandbox_mode, developer_instructions) | `export_codex.py` converts; Bash-capable agents -> `workspace-write` | TOML parsed in tests; schema from third-party adapter [M] |
| Slash commands | `.claude/commands/*.md` | (custom prompts deprecated per adapter notes) | commands exported as same-named skills | tested |
| Model switching | `/model`, not automated | own selector | router recommends; `codex_model_map` in `models.yaml` is empty on purpose | by design |
| Subagents spawning subagents | not allowed | unverified | orchestrator runs as main-thread agent (`claude --agent orchestrator`) | documented |
| Limits | - | skill body 8 KB, AGENTS.md 32 KiB (third-party reported) | `validate_skills.py` enforces 8 KB; AGENTS.md block is ~12 lines | enforced locally |
| Provider handoff | read `.aos/handoffs/<id>/` | same files | `create_handoff.py` | tested; no auto-sync |
Verify in your environment: start each tool in a project with AOS installed, ask it to list skills, run `/aos-route "fix typo"` (Claude) or invoke `aos-route` by name (Codex).
