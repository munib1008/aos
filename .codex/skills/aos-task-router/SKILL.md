---
name: aos-task-router
description: Classify task complexity (8 dimensions scored 0-3, levels 1-4) and recommend model tier and reasoning effort within the available models. Use before starting work or after repeated failures.
---
# aos-task-router
**Input**: task text (the user's request); optional `--failures N`, `--current-model ID`, `--scores '{"security":3}'`.
Run `python3 aos/scripts/route_task.py "<task>" --json` and report: level, model, effort, context budget, reasons.
- Scores are keyword heuristics: correct them when you know the real blast radius. security=3 or data_integrity=3 forces level 4; business_criticality=3 forces >= 3.
- Escalate level only for failures that produced new evidence. Never rerun an identical prompt at higher effort.
- Model switching is not assumed: present the recommendation (`action` field) and continue on the current model unless the user switches.
- `provider_note` appears only after repeated evidence-based failures: suggest `the aos-handoff`, user decides.
Models come from `aos/config/models.yaml` (`available`); keep it accurate. Thresholds are initial, calibrate with `the aos-audit`.
