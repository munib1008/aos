@AGENTS.md

# Claude Code specifics
- One master agent, one skill, one command: `claude --agent aos "<task or goal>"` or `/aos <task>`. The skill `aos` loads modules (specialist skills, role details, OMEGA spec) on demand.
- Subagents cannot spawn subagents: run `aos` as the main thread. Model switching is not automated: the router recommends, you apply it with /model.
