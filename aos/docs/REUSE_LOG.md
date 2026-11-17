# Provenance: reused / modified / independent / verified
No third-party source code is vendored or copied into this repository (checked: only the AOS files below exist; see `THIRD_PARTY_NOTICES.md`).

| Item | Class | Source (pinned) | Evidence |
|---|---|---|---|
| Skill file format (SKILL.md frontmatter rules) | **Standard adopted** | agentskills/agentskills `83021e2` (Apache-2.0) | `validate_skills.py` re-implements the spec's field rules; tests in `test_video_codex_bench.py` |
| Codex export layout + stripped Claude-only fields + TOML agent keys | **Pattern adapted, code independent** | wshobson/agents `9c61ab2` (MIT) `tools/adapters/codex.py` | `export_codex.py`; TOML parsed in tests; live Codex load **not** verified |
| Claude Code transcript usage shape (`assistant.message.usage`, 4 token fields, dedupe by message id) | **Independently confirmed** | affaan-m/ECC `3def2b6` `cost-tracker.js` + its test | AOS parser written before reading ECC; matches its documented shape; synthetic-fixture test only |
| Strategic compaction, project-scoped learning, cost-aware routing | **Ideas adapted** | ECC skills (prose) | Implemented independently in `session_manager.py`, `memory.py`, `route_task.py` |
| Methodology skills (TDD, verification, worktrees, plans) | **Recommended companion, not vendored** | obra/superpowers `22f0b46` (MIT) | No files copied |
| Advanced video editing | **External dependency by reference** | kajisho5/ffmpeg-skill `f1c97cb` (MIT) | Ran `probe.py`, `cut.py` locally - worked; its full test suite (`tests/test_all.py`) was started but **not completed** within my time limit, so its CI claims are unverified beyond the two tools run |
| Everything else (router, memory, indexer, context builder, summarizers, sessions, handoff, usage audit, benchmark, video pipeline, installers, tests, agents, skills, docs) | **Independently developed** | this repo | 48 unit tests; benchmark runs below; real FFmpeg render inspected by frame |

## Verified here (commands re-runnable)
- `python3 -m unittest discover -s aos/tests` -> 48 tests OK.
- `python3 aos/scripts/video_pipeline.py selftest` -> real 7 s mp4 rendered, verify PASSED; frames inspected (title + caption visible).
- `python3 aos/scripts/benchmark_context.py run --root <repo>` on 5 repos (see BENCHMARKS.md).
- Defect found by the benchmark and fixed: snippets omitted the target definition on large files (definition coverage 0.33/0.37 on langgraph/promptfoo before; 0.83/0.77 after) - regression test `test_symbol_deep_in_large_file_is_included`.

## Not verified
Live skill/agent loading in Claude Code and Codex; Codex 8 KB/32 KiB limits and TOML schema; end-to-end model cost; transcript parser on a real transcript; ffmpeg-skill/OpenFilm/ComfyUI integration beyond what is listed.

## Added in AOS 3.0 (TCCM + merge)
| Item | Class | Source | Evidence |
|---|---|---|---|
| Local engines (Ollama, llama.cpp, vLLM) | **Used through their OpenAI-compatible API, not bundled** | ollama `b8db8d5` MIT, llama.cpp `d3c3e24` MIT, vLLM `2440ffc` Apache-2.0 | endpoints verified in source; adapter tested against a mock server only; no real engine run |
| bifrost, Helicone, RouteLLM, Continue | **Audited, not used** | see TCCM_AUDIT.md | rationale + licenses + dates recorded |
| Specialist skills, humanizer, model-advisor, policy_guard, select_skills, procedures.md | **Reviewed and merged from the supplied builds** | uploaded aos-unified / aos-skill packages (humanizer: blader/humanizer v4.0.0, MIT) | merge report; tests; packaging verification |
| core/ (selector, usage monitor, cache, dashboard, benchmark, capacity planner, provider switch), packaging/ | **Independently developed** | this repo | 107 tests incl. mock-endpoint benchmark and 8-zip verification |
