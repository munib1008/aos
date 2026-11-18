Use when a repository has a MODULE_OWNERSHIP file or the user says Codex and Claude Code work together.
1. Read the ownership file and the contract baseline first. Your role comes from `AOS_AGENT_ROLE` or `.aos/dual/role`.
2. Work only in your own slice: `python3 aos/scripts/dualagent.py init --base <contract baseline ref> --slice <name>` creates an isolated worktree and branch. Never share a worktree with the other agent.
3. Before every push: `python3 aos/scripts/dualagent.py check --agent <you> --base <baseline>` must pass. Files owned by the other agent are changed only through a written proposal for its owner (contract/core changes are separate, backward-compatible, reviewed first).
4. Ask for the independent review: `python3 aos/scripts/dualagent.py review-packet --agent <you> --base <baseline>`, then run the commands it lists (`/codex:adversarial-review --base <ref> <focus>` in Claude Code, or `codex review "..."`). Treat the reviewer's findings as defects to verify, fix, and re-test.
5. The integrator merges; you do not merge into the protected branch. Never weaken security, business rules or contracts to ship faster.
