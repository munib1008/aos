# Codex + Claude Code as one team

Two coding agents, one repository: the **integrator/platform owner** (e.g. Codex) and the **builder** (e.g. Claude Code), each in its own worktree, each reviewing the other. AOS adds the shared rules, the ownership guard and the review plumbing. The AOS master agent (`aos`) installs into both tools, so both follow the same instructions.

## 1. Install (each step verified against the plugin's README and the real CLIs)
```text
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex          <- required; it is missing from many quick lists
/reload-plugins
/codex:setup                                 (offers to run the npm install if Codex is missing)
```
```bash
npm install -g @openai/codex                 # verified: installs codex-cli (checked version 0.170.0)
codex login                                  # or !codex login inside Claude Code; interactive, uses YOUR ChatGPT/OpenAI account
codex doctor                                 # auth, reachability, config health
```
Requirements (plugin README): Node 18.18+; a ChatGPT plan or OpenAI API key (usage counts toward your Codex limits). Plugin is Apache-2.0.
What you get: `/codex:review`, `/codex:adversarial-review` (read-only, steerable), `/codex:rescue` (delegate a task; it can EDIT files in the current checkout), `/codex:transfer` (hand this Claude session to Codex: `codex resume <id>`), `/codex:status|result|cancel`, and the `codex:codex-rescue` subagent.
AOS for both tools: `python3 aos/install.py --global --harness both` (Claude: `~/.claude/{agents,skills,commands}`; Codex: `~/.codex/{agents,skills}`).

## 2. How the pieces fit
| Need | Use |
|---|---|
| Shared rules for both agents | one `AGENTS.md` (Codex reads it; Claude reads `CLAUDE.md` -> `@AGENTS.md`) |
| Who may edit what | `MODULE_OWNERSHIP.md` + `dualagent.py check` (gate) + `dualagent.py hook` (Claude Code Edit/Write guard) |
| Isolated work | `dualagent.py init` = git worktree + branch per slice; never two agents in one checkout |
| Independent review | builder runs `/codex:adversarial-review --base <ref> <focus>` (read-only); integrator reviews the builder's PR with Codex CLI; packet from `dualagent.py review-packet` |
| Delegate a task to the other agent | `/codex:rescue` **from the other agent's worktree** (it edits the cwd) or `codex exec -C <worktree> "<task>"` |
| Continue a session in the other tool | `/codex:transfer` (Claude -> Codex) |
| Unattended multi-cycle runs | `omega_runner.py run --tool claude|codex ...` (waits out usage limits) |

## 3a. Hooks on Windows
Use the Node launcher (`templates/hooks/aos-hook.mjs`, copy to `.claude/hooks/`): `node .claude/hooks/aos-hook.mjs dual` (ownership guard) and `... omega` (risky-Bash guard). It finds `py -3`, `python` or `python3` and the AOS runtime (`.claude/aos` or `~/.claude/aos`), forwards the block code (2), and fails open with a warning if either is missing. Without `dualagent.py init`, create the role file by hand: `mkdir .aos\dual` and write `claude` into `.aos\dual\role`.

## 3. Cautions
- **Do not enable the plugin's stop-time review gate** (`/codex:setup --enable-review-gate`) in unattended or OMEGA runs: the plugin itself warns it can create a long Claude/Codex loop that drains usage limits.
- `/codex:rescue` and `codex exec` write to the checkout they run in: run them in the owner's worktree, not the builder's.
- Both tools draw on separate provider allowances; AOS never pools, bypasses or evades them.
- The ownership hook covers Edit/Write tools only; the `check` gate (CI/PR review) is authoritative.
- Authentication (`codex login`) is yours to do; AOS never handles credentials.

## 4. Verified here vs not
Verified: plugin repo, marketplace name `openai-codex`, plugin `codex@openai-codex` and its commands (read from source); `npm install -g @openai/codex` and CLI flags (`exec`, `exec resume --last`, `review`, `-C`, `--sandbox`); the ownership parser on a real `MODULE_OWNERSHIP.md`; 9 tests. **Not verified**: running the plugin inside Claude Code and any Codex run (needs your login; the build sandbox cannot reach the provider).
