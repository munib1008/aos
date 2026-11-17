# AOS downloads
Pick ONE form per profile. `default` = full generic build (incl. capacity/cost routing). `dormakaba` = policy-restricted build (AI Platform catalog only; no local runtimes, free tiers, credits registry or auto model switching).

| File | Use it as | Install |
|---|---|---|
| `aos-<profile>-skill.zip` | one Agent Skill with scripts (Claude Code or Codex) | unzip so the folder is `~/.claude/skills/aos/` (or `.codex/skills/aos/`) |
| `aos-<profile>-skill-noscript.zip` | claude.ai skill upload (no code allowed) | Settings > Capabilities > Skills > upload |
| `aos-<profile>-claude-code.zip` | skills + agents + slash commands for Claude Code | unzip at the project root; run `claude --agent orchestrator` for delegation |
| `aos-<profile>-codex.zip` | skills + agents (TOML) for Codex | unzip at the project root |

Verify downloads with `sha256sum -c SHA256SUMS`. Scripts need Python 3.9+ (standard library only). Not verified here: live loading in Claude Code, Codex and claude.ai; end-to-end cost savings. See `docs/BENCHMARKS.md` and `docs/COMPATIBILITY.md` inside the zips.
