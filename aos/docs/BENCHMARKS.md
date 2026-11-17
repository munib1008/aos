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
