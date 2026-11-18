# Project OMEGA - part of the one `aos` master agent

OMEGA is the goal-driven mode of the single AOS master agent (`claude --agent aos`). Everything below applies to that agent. One agent you can call from any repository: it audits what really exists, completes and hardens it, and keeps going until the acceptance criteria are **verified with evidence**. It does not stop to ask for routine approvals, and it waits out provider usage limits instead of failing.

## Install once, call anywhere
```bash
python3 aos/install.py --global --harness both            # ~/.claude/{agents,skills,commands} + ~/.codex/..., runtime in ~/.claude/aos
python3 aos/scripts/omega_runner.py setup-hook --global    # guard hook in ~/.claude/settings.json (verify with /hooks)
```
Or unzip a `*-claude-code.zip` / `*-codex.zip` from `dist/` into a project (project-local). Each installs exactly one agent (`aos`), one skill (`aos`) and one command (`/aos`).

## Three ways to run it
| Way | Command | Use |
|---|---|---|
| Interactive | `claude --agent aos "Complete <project>; accept: ..."` or `/aos <goal>` | you watch, it works |
| Unattended | `python3 ~/.claude/aos/scripts/omega_runner.py run --goal "..." --accept "..." [--repo URL]` | loops headless cycles until `omega.py done`; waits on limits |
| Scheduled | cron/Task Scheduler runs the same `omega_runner.py run ...` every 30 min | restarts after long waits; a lock prevents double-runs |
Generic tools: `--tool codex` or `--cmd "mytool --prompt {prompt}"`. Stop cleanly: `omega_runner.py stop`. Look: `omega_runner.py status`.

## "Without requesting approval" - what that means here
- **Routine, reversible work** inside the goal (edit, test, refactor, add files, local commits, local/dev migrations) runs with no approval prompt.
- **Guarded actions** are never run on a guess and never block the loop: force-push, `reset --hard`, deletes, DROP/TRUNCATE, non-local migrations, deploy/publish/merge, cloud/IAM changes, sudo, global installs, pushes to protected branches. The guard returns `queue`: the action is written to `.aos/omega/approvals.json` and the agent continues with independent items. You review later: `omega.py approvals list`, then `omega.py approvals resolve P1 --approve --by you` (the agent may then run that exact command once). **Hard-denied** actions (pipe-to-shell, `rm -rf /`, disk wipes, reading SSH/cloud credential files) are refused.
- Production release always needs a human-approved `production_release` entry; the gate never reports "production" without it.
- This matches your own master prompt ("approval required for destructive changes, business-policy decisions, production deployment, security-sensitive external actions") and AOS's safety rules. Rules live in `config/omega.yaml`.
- Permission prompts of the harness itself: use `--permission-mode auto` (default in the runner) or an allowlist. Do not run with permission checks disabled outside a disposable container; the guard hook is your safety net, and it only covers the Bash tool.

## Usage limits: wait and continue
On a limit message (`usage limit reached`, `5-hour limit`, HTTP 429, `try again in ...`, `resets 3pm (Europe/Dubai)`, epoch resets) the runner: saves `CHECKPOINT.md`, computes the reset time (config backoff 5m -> 2h if none is given), sleeps until reset + margin in interruptible 60 s steps, then resumes with the short continuation prompt (`--continue` for Claude). Waits longer than `max_wait_hours` (weekly limits) exit with code 75 and a resume time in `runner.json` for the scheduler. It never switches accounts, retries early, or hides the limit. Transient overload/5xx errors use short waits and give up after repeated failures (exit 23).
Inside an interactive session the agent checkpoints and, if a scheduling tool exists (ScheduleWakeup, send_later, /loop), schedules the resume; otherwise rerun it - state is on disk.

## State, evidence and the gate
`.aos/omega/`: `state.json` (goal, acceptance, features), `backlog.json` (dependency graph, priority breakdown), `scorecard.json`, `approvals.json`, `runs.jsonl`, `CHECKPOINT.md`. `omega.py report` writes `docs/implementation/MASTER_BACKLOG.md` and `COMPLETION_STATUS.md`. Completion needs evidence the tool can check (`test:<cmd> => PASSED`, `file:`, `commit:`, `doc:`, `url:`, `run:`); fabricated or later-broken evidence fails `omega.py gate`. Score weights (functional 20, correctness 15, security 15, UX 15, architecture 10, performance 10, tests 10, docs/ops 5) and level thresholds (staging 60 / UAT 75 / production 85) come from your spec; **the thresholds are my assumptions**. Critical or high security/data items open = `not_ready` regardless of score. The runner stops when `omega.py done` is true, or on stall (no change for 3 cycles), 200 cycles, or repeated failures.

## What is verified and what is not
Verified here: ledger, evidence checks, priority/dependency order, gate levels, guard rules, hook protocol logic, limit parsing, wait/resume/stall/lock behavior (29 tests, including a real child process that hits a limit, waits, resumes and finishes), and that the installed Claude Code (2.1.327) has the flags the preset uses and loads the master agent. **Not verified**: a real provider usage limit end to end; the guard hook firing inside live Claude Code (a nested permission-bypass test was refused by the safety classifier and not worked around); the Codex preset (Codex not installed); unattended runs on a real repository such as dk-Service (this sandbox has no access to it); "1000x" - OMEGA measures against a baseline, it does not guarantee a multiple.
