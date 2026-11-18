<!-- aos-local-inference: Use local open-weight models (Ollama, llama.cpp server, vLLM) for suitable low-risk tasks - detect engines, recommend model sizes for the machine, call a local OpenAI-compatible endpoint, verify outpu -->
# aos-local-inference
AOS reuses existing engines; it does not ship one. All three expose an OpenAI-compatible `/v1` API, so one stdlib client covers them (`core/compute/local_model_adapter.py`). Loopback/private hosts only; local traffic bypasses any proxy.

## Steps
1. `python3 aos/scripts/tccm.py inventory` -> engines (running/installed/models) and the largest model size class that fits (`est_memory_gb`, estimate not measurement). Verify current model releases and **licences** (some restrict commercial use) before downloading. Set `OLLAMA_NO_CLOUD=1` if you use Ollama and want no cloud features.
2. Suitable: classification, summarization, documentation, repo-search assistance, code explanation (>=7B), small tested changes, routine transformation. **Not suitable without independent verification by a stronger model**: security-critical, level>=3, multi-module design, anything without tests.
3. Route: `tccm.py route "<task>" --mode local_first` (it only offers a local model if category, size, level and p_success allow).
4. Verify every local output with tests/linters; escalate to the next candidate only with NEW evidence (`model_selector.next_escalation`).
5. Measure before trusting: `tccm.py bench run --target name,local,http://localhost:11434/v1,<model> --repeats 3` (deterministic graders; `python_tests` executes model code in a subprocess - not a sandbox; use a container/VM). Compare with a cloud target or `bench import` results from your harness.

## Output contract
Chosen engine/model, memory fit, measured completion rate and latency (or "not measured"), verification result.

## Safety
CPU-only generation is bandwidth-bound: measure tokens/s first. Never send confidential code to a remote endpoint labelled "local" (adapter refuses non-local URLs).
