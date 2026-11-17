---
name: aos-validation
description: Validate changes and the AOS system itself - focused tests, regression checks, honest test summaries, memory validation, and baseline-vs-AOS efficiency audit. Use after code changes and for the aos-audit.
---
# aos-validation
**Code**: `python3 aos/scripts/summarize_changes.py` (risk flags) -> `python3 aos/scripts/summarize_tests.py run -- <cmd>`. Order: affected tests, type/lint, broader regression for shared/auth/data/migration changes. Report PASSED / FAILED / NOT VERIFIED; unparsed non-zero exit is FAILED.
**Quality gate**: acceptance criteria met, critical tests pass, no new unauthorized access, no data loss, maintainable, no regression.
**Memory**: `python3 aos/scripts/validate_memory.py --check-sources`.
**AOS self-test**: `python3 -m unittest discover -s aos/tests`.
**Audit (the aos-audit)**: `measure_usage.py transcript --latest` for real usage; `measure_usage.py record ...` per benchmark run (baseline vs aos); `measure_usage.py compare`. Report only measured numbers; verdict REJECT when pass rate or regressions worsen. See aos/docs/BENCHMARKS.md.
