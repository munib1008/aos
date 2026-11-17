# Benchmark protocol (measure, never estimate)

Tasks (pick real ones from your projects; keep the same repo commit for both variants):
A text/UI change - B localized TS bug - C new API endpoint - D schema change - E React component - F auth/permission issue - G failing CI investigation - H workflow integration - I multi-module task - J handoff + resume.

For each task run >=3 times per variant on a clean checkout of the same commit:
- **baseline**: AOS commands/skills/agents not installed, same model/effort.
- **aos**: installed, normal use.

Capture per run: `python3 aos/scripts/measure_usage.py transcript --latest` (input/output/cache tokens, model calls, tool calls from the Claude Code transcript), wall time, retries, acceptance tests passed (true/false), regressions, human corrections. Record:
```bash
python3 aos/scripts/measure_usage.py record --task B-localized-bug --variant aos --source transcript \
  --input-tokens 52000 --output-tokens 4100 --cache-read-tokens 30000 --model-calls 14 --tool-calls 22 \
  --seconds 210 --retries 0 --passed true --regressions 0 --human-corrections 0
python3 aos/scripts/measure_usage.py compare --min-runs 3
```
(Numbers above are format examples, not results.) Verdicts: ACCEPT, PRELIMINARY (<3 runs), REJECT (pass rate or regressions worse), INCOMPLETE, UNKNOWN. Recalibrate `optimization.yaml` thresholds from these results; keep policy versions in git.
Targets to test (not promises): -20-40% avoidable context, -15-30% repeated scans, lower cost per *successful* task, equal/better pass rate.

## Measured so far: context-retrieval benchmark (offline, reproducible)
`python3 aos/scripts/benchmark_context.py run --root <repo> --n 30 --seed 7` - 30 tasks per repo generated from real symbols (query = words of a symbol name, expected = defining file). Baseline "naive" = grep the words, read the top 5 files in full. Run 2026-11-17 on audited clones (pinned commits in REUSE_LOG.md):

| Repo | Files | naive tokens | naive recall / def | AOS tokens | AOS recall / def | AOS/naive tokens |
|---|---|---|---|---|---|---|
| GlobeVPN (Kotlin) | 97 | 9,362 | 0.87 / 0.97 | 2,274 | 1.00 / 1.00 | 0.24 |
| openfilm (TS) | 2,812 | 159,353 | 0.47 / 0.67 | 5,818 | 1.00 / 1.00 | 0.04 |
| ffmpeg-skill (Py) | 425 | 407,178 | 0.47 / 0.83 | 3,854 | 0.97 / 0.97 | 0.01 |
| langgraph (Py) | 707 | 208,567 | 0.17 / 0.37 | 2,046 | 0.83 / 0.83 | 0.01 |
| promptfoo (TS) | 6,054 | 593,943 | 0.07 / 0.10 | 4,538 | 0.77 / 0.77 | 0.01 |

"recall" = defining file found; "def" = the symbol's definition appears in the loaded context. Tokens = chars/4 estimates.
**What this does and does not show.** It shows the deterministic retrieval layer loads far less text and finds the right code more often than an unguided grep-and-read. It does **not** show lower end-to-end cost: queries contain the symbol's words (flatters both methods), the baseline is a strawman-ish heuristic (a careful human/agent reads less than "5 full files"), and there is no measurement of model answers, retries, or overhead from AOS's own tool calls. The 20-40% "avoidable context" target is therefore **not yet validated**; the per-task A/B above is the next step. The benchmark already found and drove the fix of one real defect (see REUSE_LOG.md).
