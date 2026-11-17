@AGENTS.md

# Claude Code specifics
- Slash commands: /aos-optimize /aos-scan /aos-route /aos-learn /aos-fork /aos-resume /aos-handoff /aos-audit (skills in `.claude/skills`, agents in `.claude/agents`).
- Subagents cannot spawn subagents: use the `orchestrator` agent as the main thread (`claude --agent orchestrator`) when delegation is wanted; otherwise the aos-optimize skill runs inline.
- Model switching is not automated: the router recommends, you apply it with /model.
