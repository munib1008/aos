# Run the local-vs-cloud benchmark (about 10 minutes)

Needs: Python 3.9+, a local engine with a model, optionally a cloud OpenAI-compatible endpoint. Everything else is in the repo.

1. **Size a model for your machine**: `python3 aos/scripts/tccm.py inventory` (look at `local_recommendation`; check the model's licence before pulling).
2. **Start the engine and pull one model** (Ollama example): `ollama serve` (set `OLLAMA_NO_CLOUD=1` if you want no cloud features), then `ollama pull <model>`. llama.cpp: `llama-server -m <file.gguf> --port 8080` (use `--url http://localhost:8080/v1`).
3. **Check it**: `python3 aos/scripts/tccm.py bench doctor --url http://localhost:11434/v1 [--model <model>]` -> reachable, models, one smoke prompt.
4. **Run and compare** (3 repeats per task, 9 tasks):
```bash
python3 aos/scripts/tccm.py bench run --repeats 3 --report-out benchmark_report.md \
  --target local,local,http://localhost:11434/v1,<local-model> \
  --target cloud,paid_api,<openai-compatible-url>,<cloud-model>,<usd_in_per_mtok>,<usd_out_per_mtok>
```
   Cloud keys: put the key in the environment variable your gateway expects and never in files; add `api_key_env` handling by editing the target dict if your gateway needs auth (the CLI reads no keys). Without prices the cloud cost stays "unknown" (token counts are still compared).
5. **Subscription models (Claude Code, Codex)** cannot be called from scripts: run the same prompts through the harness, write one JSON row per task (`target, kind: subscription, model, task, category, level, success, first_try, attempts, latency_s, input_tokens, output_tokens, tokens_estimated, cost_usd_estimated: null, note`) to a `.jsonl` file and `tccm.py bench import results.jsonl`.
6. **Read the verdict**: `python3 aos/scripts/tccm.py bench report`. Offload only the categories marked reliable (>=80% with >=3 samples), always verify local output with tests, and re-run after changing models. `tccm.py plan` then converts measured local reliability into movable tokens from your ledger.

Caveats: 9 small tasks are a smoke-level signal, not a leaderboard; extend `core/benchmark/suite_basic.json` with tasks from your projects. `python_tests` executes model-written code in a subprocess without a sandbox: use a container or VM for untrusted models. Electricity and hardware wear are not counted in "local" cost.
