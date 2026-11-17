"""Capacity planning from MEASURED data only. Each lever reports evidence = measured | no_data. No combined % is produced and no
dollar saving is claimed unless real measurements exist; otherwise it says exactly what data is missing."""
from __future__ import annotations

import json
from pathlib import Path

import core  # noqa: F401
from aos_lib import load_config, state_dir
from core.budget import usage_monitor as um
from core.cache import context_cache as cc


def plan(root: Path) -> dict:
    cfg = load_config("routing", root)
    rows = um.entries(root)
    levers = []
    # 1. context reduction (retrieval benchmark, a proxy)
    files = sorted((state_dir(root) / "benchmarks").glob("context-*.json")) if (state_dir(root) / "benchmarks").exists() else []
    if files:
        j = json.loads(files[-1].read_text())
        levers.append({"lever": "Reduce redundant context (index+snippets vs grep+read)", "evidence": "measured (retrieval proxy)", "naive_mean_tokens": j["naive"]["mean_tokens"],
                       "aos_mean_tokens": j["aos"]["mean_tokens"], "ratio": j["token_ratio_aos_over_naive"],
                       "caveat": "context-loading only; apply to your spend only after an end-to-end A/B (docs/BENCHMARKS.md)"})
    else:
        levers.append({"lever": "Reduce redundant context", "evidence": "no_data", "needs": "run benchmark_context.py on your repo"})
    # 2. local offload
    local_cats = set(cfg["task_categories"]["local_suitable"])
    cloud = [r for r in rows if r["kind"] in ("subscription", "paid_api", "free_tier") and r.get("category") in local_cats]
    qpath = state_dir(root) / "benchmarks" / "model_quality.jsonl"
    qrows = [json.loads(l) for l in qpath.read_text().splitlines() if l.strip()] if qpath.exists() else []
    localq = [r for r in qrows if r["kind"] == "local"]
    addressable = sum(r["input_tokens"] + r["output_tokens"] for r in cloud)
    if not cloud:
        levers.append({"lever": "Move suitable tasks to a local model", "evidence": "no_data", "needs": "ledger entries with categories (usage_monitor.record(category=...))"})
    elif not localq:
        levers.append({"lever": "Move suitable tasks to a local model", "evidence": "no_data", "addressable_cloud_tokens_measured": addressable,
                       "needs": "a measured local-model success rate (model_quality benchmark on this machine); without it no saving can be claimed"})
    else:
        rate = sum(1 for r in localq if r["success"]) / len(localq)
        levers.append({"lever": "Move suitable tasks to a local model", "evidence": "measured", "addressable_cloud_tokens_measured": addressable, "local_success_rate_measured": round(rate, 3),
                       "n_local_samples": len(localq), "tokens_movable_if_verified": round(addressable * rate),
                       "caveat": "assumes ledger categories match benchmark categories; failed local attempts cost time and may need cloud rework"})
    # 3. cache
    st = cc.stats(root)
    levers.append({"lever": "Exact-match cache (valid-state only)", "evidence": "measured" if (st["hits"] + st["misses"]) else "no_data", "hits": st["hits"], "misses": st["misses"],
                   "hit_rate": st["hit_rate"], "saved_tokens_estimated": st.get("saved_tokens_est", 0), "duplicate_tool_calls_avoided": st.get("duplicate_calls", 0)})
    # 4. repeated failures
    failed = [r for r in rows if r.get("success") is False]
    levers.append({"lever": "Prevent repeated failed attempts", "evidence": "measured" if rows else "no_data", "failed_entries": len(failed),
                   "tokens_in_failed_entries": sum(r["input_tokens"] + r["output_tokens"] for r in failed), "retried_entries": sum(1 for r in rows if r["retries"] > 0)})
    # 5. agent spawning
    sub = [r for r in rows if (r.get("category") or "") == "subagent"]
    levers.append({"lever": "Limit unnecessary agent spawning", "evidence": "measured" if sub else "no_data", "subagent_entries": len(sub),
                   "needs": None if sub else "record subagent calls with category='subagent' to quantify"})
    return {"levers": levers, "claim": "No aggregate saving is claimed. Only measured levers above carry numbers; each states its caveat.",
            "measured_levers": sum(1 for l in levers if str(l["evidence"]).startswith("measured"))}
