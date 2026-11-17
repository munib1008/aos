# TCCM audit - local inference, gateways, routing (2026-11-17)

Method as in BUILD_VS_REUSE.md: shallow clones in an isolated scratch dir, nothing executed, facts via `scripts/audit_repo.py`, claims confirmed in code. Maintenance = HEAD commit date of the clone.

| Repo (pinned) | License | HEAD | Scale | Verified in code | Security / privacy | Hardware | Decision |
|---|---|---|---|---|---|---|---|
| ollama/ollama `b8db8d5` | MIT | 2026-11-16 | 1,660 files, 531 tests | OpenAI-compatible `POST /v1/chat/completions`, `GET /v1/models` in `server/routes.go`; `OLLAMA_NO_CLOUD` env disables cloud features (`envconfig`) | Has optional cloud features: set `OLLAMA_NO_CLOUD=1` | CPU or GPU; single Go binary | **Preferred local engine** (user-installed, optional) |
| ggml-org/llama.cpp `d3c3e24` | MIT | 2026-11-16 | 4,198 files, 338 tests | `llama-server` documents OpenAI-compatible `/v1/models` and chat; `cache_prompt` (default true) and `--cache-reuse` prompt/KV reuse | Its own docs warn prompt-cache reuse can make results nondeterministic -> AOS exact-match cache requires temperature 0 and is keyed on state | Best on CPU-only / small RAM; cmake build | **Alternate engine** (low-resource machines) |
| vllm-project/vllm `2440ffc` | Apache-2.0 | 2026-11-17 | 9,464 files, 2,942 tests | Prefix caching documented; CPU install page exists | **Usage-stats telemetry on by default** (`usage_lib.is_usage_stats_enabled`; disable with `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1` or `~/.config/vllm/do_not_track`) | Built for GPU servers | **Not bundled**; adapter works if you run it on a GPU box |
| maximhq/bifrost `6fce06e` | Apache-2.0 | 2026-11-16 | 6,946 files, 2,049 tests | `plugins/governance` (budget tests), `plugins/semanticcache`, `plugins/routing` exist | Gateway for API-key traffic; 66 files match telemetry patterns, not reviewed | Go service | **Defer**: duplicates AOS ledger/selector unless you run many API-billed agents |
| lm-sys/RouteLLM `0b64fda` | Apache-2.0 | **2024-08-10** (15 months stale) | 108 files, 2 tests | Binary strong/weak router; `mf` router calls OpenAI `text-embedding-3-small` | Sends prompts to OpenAI for embeddings; torch+transformers+litellm deps | CPU/GPU | **Reject**. Concept kept: calibrate on measured success rates (AOS uses per-model measured stats) |
| continuedev/continue `5522c6f` | Apache-2.0 | 2026-07-20 | 3,058 files, 445 tests | IDE extension / coding agent | 37 files match telemetry patterns, not reviewed | - | **No integration**: it is a consumer of models; it can use the same local endpoints on its own |
| Helicone/helicone `5a21c3a` | Apache-2.0 | 2026-11-16 | 4,750 files, 190 tests | AI gateway + observability; self-host compose stack (web, worker, clickhouse, minio, supabase dirs) | Routes provider traffic through the gateway; heavy stack | Docker stack | **Reject for core**: proxying + heavy ops; local ledger covers accounting |

## Selected combination (smallest that works)
- **Zero new runtime dependencies.** One stdlib OpenAI-compatible client (`core/compute/local_model_adapter.py`) talks to Ollama, llama.cpp or vLLM, whichever the user already runs. Nothing is vendored.
- Routing, budgets, cache, dashboard, benchmark are AOS-native (`core/`), because the audited gateways proxy traffic and none fits subscription-based agents (Claude Code, Codex), where AOS must recommend rather than intercept.
- Overlap rule: do not run two of {Ollama, llama.cpp, vLLM} unless a measured benefit exists.

## This machine (measured by `tccm.py inventory`)
4 vCPU Xeon (AVX2/AVX512/AMX), 15.7 GB RAM, no GPU, 25 GB free disk, no local engine installed, model hubs (Hugging Face, ollama.com) unreachable from the sandbox. Size recommendation: up to ~8B at Q4 (estimate). **No real local model could be run here**, so no local-model quality, latency or cost-per-success number exists yet.

## Assumptions and risks
Model licences vary (some restrict commercial use): verify before download. CPU-only speed is memory-bandwidth-bound: measure. Free-tier terms change: AOS requires a dated verification before routing to one. Priors for p_success are uncalibrated until `tccm.py bench` has real runs.
