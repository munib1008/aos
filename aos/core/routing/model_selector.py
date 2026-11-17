"""Adaptive model selector: category -> complexity -> eligible models -> expected cost per SUCCESS -> mode policy -> budget gate.

Decision values: deterministic | execute | needs_authorization | no_candidate.
Nothing is executed here and nothing is proxied: the result tells the harness (Claude Code / Codex) or the local adapter what to run.
Success probabilities are MEASURED (ledger/benchmark, >= min_samples) when available, else uncalibrated PRIORS (flagged in output).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import core  # noqa: F401
from aos_lib import load_config
from core.budget import usage_monitor as um
from route_task import route

CATEGORY_RULES = [
    ("repo_search", r"\b(where is|find (the )?(file|function|class|usage|code)|locate|which file|search (the )?(repo|code))\b"),
    ("test_summary", r"\b(summari[sz]e|parse)\b.*\b(test|log|ci)\b"),
    ("diff_summary", r"\b(summari[sz]e|describe)\b.*\b(diff|changes|commit)\b"),
    ("small_change", r"\b(fix (a )?typo|tweak|one-?line|update (the )?(label|text|copy|constant)|rename)\b"),
    ("security_critical", r"\b(auth|oauth|permission|rls|tenant|crypto|encrypt|secret|token (refresh|rotation)|vulnerab|exploit|payment|ledger|credential)\b"),
    ("classification", r"\b(classify|categori[sz]e|triage|sentiment|label (each|these|every))\b"),
    ("documentation", r"\b(docs?|documentation|readme|docstring|changelog|comment)\b"),
    ("summarization", r"\b(summari[sz]e|tl;?dr|digest)\b"),
    ("code_explanation", r"\b(explain|what does|how does|walk me through)\b"),
    ("transform", r"\b(convert|reformat|translate|rename|migrate (format|file)|extract)\b"),
    ("architecture", r"\b(architecture|redesign|re-?architect|design (the|a) system|multi-?module)\b"),
    ("debugging", r"\b(bug|crash|fail(ing|s)?|regression|intermittent|flaky|error)\b"),
    ("feature", r"\b(add|implement|build|create|feature|endpoint)\b"),
]
RANK = {"local": 0, "subscription": 1, "free_tier": 1, "credits": 2, "paid_api": 3}


def categorize(task: str) -> str:
    for cat, rx in CATEGORY_RULES:
        if re.search(rx, task, re.I):
            return cat
    return "feature"


def _catalog(root: Path | None, local_models: dict[str, list[str]] | None) -> list[dict]:
    """Flatten providers.yaml (+ runtime-discovered local models) into candidate rows."""
    provs = (load_config("providers", root).get("providers") or {})
    rows = []
    for pid, p in provs.items():
        kind = um.norm_kind(p["kind"])
        enabled = p.get("enabled")
        models = dict(p.get("models") or {})
        if kind == "local":
            found = (local_models or {}).get(pid) or []
            enabled = bool(found) if enabled == "auto" else bool(enabled and found)
            for m in found:
                models.setdefault(m, {"tier": (p.get("model_tiers") or {}).get(m, "local_mid"), "price_per_mtok": None})
        if enabled is not True and not (kind == "local" and enabled):
            continue
        for mid, meta in models.items():
            rows.append({"provider": pid, "kind": kind, "model": mid, "tier": meta.get("tier", "balanced"), "price": meta.get("price_per_mtok"), "cost_multiplier": meta.get("cost_multiplier"),
                         "context_tokens": meta.get("context_tokens"), "terms_verified_on": p.get("terms_verified_on"),
                         "commercial_ok": p.get("commercial_use_allowed"), "data_policy": p.get("data_policy", "unverified"),
                         "allowance": p.get("allowance")})
    return rows


def _eligibility(c: dict, ctx: dict, cfg: dict) -> str | None:
    """Return a reason string if the candidate must be excluded."""
    if c["kind"] == "free_tier":
        v = c["terms_verified_on"]
        if not v:
            return "free-tier terms not verified (set terms_verified_on in providers.yaml)"
        try:
            age = (date.today() - date.fromisoformat(str(v))).days
        except ValueError:
            return "terms_verified_on is not a valid date"
        if age > ctx["max_terms_age_days"]:
            return f"free-tier terms last verified {age} days ago (> {ctx['max_terms_age_days']}): re-verify, allowances change"
        if ctx["commercial"] and c["commercial_ok"] is not True:
            return "commercial use not confirmed for this free tier"
    if ctx["confidential"] and c["kind"] != "local" and c["data_policy"] not in ("no_training",):
        return f"confidential task: provider data policy is '{c['data_policy']}' (needs verified no_training) - use local or a verified provider"
    if c["kind"] == "free_tier" and c["data_policy"] != "no_training" and ctx["category"] in ("security_critical",):
        return "security-critical work must not go to a free tier with unverified data handling"
    if c["kind"] == "local":
        need = cfg["task_categories"]["local_suitable"].get(ctx["category"] if ctx["category"] != "repo_search" else "repo_search_assist")
        if need is None and ctx["category"] not in ("security_critical",):
            return f"category '{ctx['category']}' is not on the local-suitable list"
        if ctx["category"] == "security_critical":
            return "security-critical tasks are not assigned to local models without independent verification (use as helper only)"
        if ctx["level"] >= 3:
            return f"level {ctx['level']} task exceeds what local models are trusted with"
        size = _size_b(c["model"])
        if size is not None and need is not None and size < need:
            return f"local model ~{size}B is below the {need}B minimum for {ctx['category']}"
    rt = cfg["routing"]
    floor = rt["min_tier_by_level"].get(ctx["level"])
    if floor and c["kind"] != "local" and rt["tier_rank"].get(c["tier"], 1) < rt["tier_rank"][floor]:
        return f"level {ctx['level']} work requires tier >= {floor} (model tier is {c['tier']})"
    if c["context_tokens"] and ctx["input_tokens"] > c["context_tokens"]:
        return "context larger than model window"
    return None


def _size_b(name: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", name.replace("-", " ").replace(":", " ").replace("_", " "))
    return float(m.group(1)) if m else None


def p_success(root: Path | None, c: dict, ctx: dict, cfg: dict) -> tuple[float, str]:
    st = um.success_stats(root, c["model"], ctx["category"]) if root else {"n": 0}
    if st["n"] >= cfg["priors"]["min_samples"] and st["success_rate"] is not None:
        return max(0.01, st["success_rate"]), f"measured n={st['n']}"
    tier = c["tier"]
    table = cfg["priors"]["success"].get(tier) or cfg["priors"]["success"]["balanced"]
    return table[ctx["level"]], "prior (uncalibrated)"


def cost_est(c: dict, ctx: dict, cfg: dict) -> float | None:
    e = cfg["estimation"]
    out_t = min(e["max_output_tokens"], int(ctx["input_tokens"] * e["output_to_input_ratio"]))
    if c["kind"] == "local":
        return round((ctx["input_tokens"] + out_t) * e["local_cost_usd_per_mtok"] / 1e6, 6)
    if c["kind"] in ("subscription", "free_tier", "credits"):
        return 0.0 if c["kind"] != "credits" else um.estimate_cost(c["price"], ctx["input_tokens"], out_t, ctx["cached_tokens"])
    return um.estimate_cost(c["price"], ctx["input_tokens"], out_t, ctx["cached_tokens"])


def select(task: str, *, root: Path | None = None, mode: str | None = None, input_tokens: int | None = None, cached_tokens: int = 0,
           confidential: bool = False, commercial: bool = True, local_models: dict[str, list[str]] | None = None,
           category: str | None = None, scores: dict | None = None) -> dict:
    cfg = load_config("routing", root)
    mode = mode or cfg["default_mode"]
    if mode not in ("local_first", "balanced", "quality_first"):
        raise ValueError("mode must be local_first|balanced|quality_first")
    r = route(task, scores_override=scores, root=root)
    cat = category or categorize(task)
    r["level"] = max(r["level"], cfg["routing"]["category_min_level"].get(cat, 1))
    ctx = {"category": cat, "level": r["level"], "input_tokens": input_tokens or r["context_budget_tokens_est"], "cached_tokens": cached_tokens,
           "confidential": confidential, "commercial": commercial, "max_terms_age_days": 90}
    base = {"task": task, "category": cat, "level": r["level"], "mode": mode, "scores_heuristic": r["scores_are_heuristic"]}
    if cat in cfg["task_categories"]["deterministic_first"]:
        tool = {"repo_search": "index_repository.py query", "test_summary": "summarize_tests.py parse", "diff_summary": "summarize_changes.py", "lookup": "memory.py search"}[cat]
        return {**base, "decision": "deterministic", "tool": f"aos/scripts/{tool}", "reasons": ["deterministic tool before any LLM call"]}
    est = cfg["estimation"]
    excluded, ranked = [], []
    for c in _catalog(root, local_models):
        why = _eligibility(c, ctx, cfg)
        if why:
            excluded.append({"model": f"{c['provider']}/{c['model']}", "reason": why})
            continue
        p, src = p_success(root, c, ctx, cfg)
        cost = cost_est(c, ctx, cfg)
        lat = est["latency_prior_s"]["local" if c["kind"] == "local" else "cloud"]
        if root:
            st = um.success_stats(root, c["model"], cat)
            lat = st["mean_latency_s"] or lat
        attempts = min(est["max_attempts"], 1 / p)
        rank_cost = cost
        if c["kind"] in ("subscription", "credits") and (cost in (0.0, None)) and c["price"] is None:
            sh = (c["cost_multiplier"] * est.get("shadow_usd_per_mtok_per_cost_multiplier", 2.0)) if c.get("cost_multiplier") is not None else est["shadow_usd_per_mtok_by_tier"].get(c["tier"], 2.0)
            rank_cost = (ctx["input_tokens"] + min(est["max_output_tokens"], int(ctx["input_tokens"] * est["output_to_input_ratio"]))) * sh / 1e6
        ecps = None if rank_cost is None else round(rank_cost / p + (1 - p) * est["failure_penalty_usd"] + lat / 60 * est["latency_penalty_usd_per_min"], 6)
        ranked.append({**{k: c[k] for k in ("provider", "kind", "model", "tier")}, "p_success": round(p, 3), "p_source": src, "est_cost_usd": cost,
                       "expected_attempts": round(attempts, 2), "ecps": ecps, "latency_s": round(lat, 1)})
    if not ranked:
        return {**base, "decision": "no_candidate", "excluded": excluded, "reasons": ["no eligible model: enable a provider in providers.yaml or start a local engine"]}

    def key_balanced(x):  # unknown price sorts after known; subscription/local free
        return (x["ecps"] is None, x["ecps"] if x["ecps"] is not None else 0, RANK[x["kind"]], x["model"])
    mcfg = cfg["modes"].get(mode) or {}
    if mode == "local_first":
        loc = [x for x in ranked if x["kind"] == "local" and x["p_success"] >= mcfg.get("min_p_local", 0.5)]
        mult = mcfg.get("non_local_cost_multiplier", 3.0)
        ordered = sorted(loc, key=key_balanced) + sorted([x for x in ranked if x not in loc], key=lambda x: (x["ecps"] is None, (x["ecps"] or 0) * (mult if x["kind"] != "local" else 1), RANK[x["kind"]]))
    elif mode == "quality_first" and r["level"] >= (mcfg.get("difficult_level", 3)):
        ordered = sorted([x for x in ranked if x["kind"] != "local"] or ranked, key=lambda x: (-x["p_success"], x["ecps"] is None, x["ecps"] or 0)) + [x for x in ranked if x["kind"] == "local"]
    else:
        ordered = sorted(ranked, key=key_balanced)
    chosen = ordered[0]
    gate = um.check(root, chosen["kind"], chosen["est_cost_usd"], mode) if root else {"decision": "allow", "reasons": ["no project root: budget not checked"]}
    decision, reasons = ("execute", []) if gate["decision"] == "allow" else ("needs_authorization", gate["reasons"])
    alt = None
    if decision == "needs_authorization":      # offer the best candidate that needs no authorization
        for x in ordered[1:]:
            if um.check(root, x["kind"], x["est_cost_usd"], mode)["decision"] == "allow":
                alt = x
                break
    reasons += [f"{chosen['kind']} model chosen by {mode} policy: p_success {chosen['p_success']} ({chosen['p_source']}), est cost {chosen['est_cost_usd']}"]
    verification = None
    if cat == "security_critical" or r["level"] >= 4:
        verification = "independent verification by a different strong model + security tests required; human approval for deployment"
    elif chosen["kind"] == "local":
        verification = "run the project's tests/linters on the output; escalate to a cloud model on failure with NEW evidence"
    return {**base, "decision": decision, "choice": chosen, "alternative_without_authorization": alt, "escalation_path": [x for x in ordered[1:4]],
            "excluded": excluded, "reasons": reasons, "verification": verification, "budget": gate,
            "assumptions": [f"p_success from {chosen['p_source']}", "token counts are estimates (chars/4) unless measured", "price None => cost unknown, never treated as free"]}


def next_escalation(selection: dict, failure_evidence: str, previous_evidence: list[str] | None = None) -> dict:
    """Escalate only with NEW evidence (never an identical retry)."""
    if not failure_evidence.strip():
        return {"escalate": False, "reason": "no evidence supplied: investigate the failure first"}
    if failure_evidence.strip() in (previous_evidence or []):
        return {"escalate": False, "reason": "identical to a previous failure: change the hypothesis/approach, do not just retry on a bigger model"}
    path = selection.get("escalation_path") or []
    if not path:
        return {"escalate": False, "reason": "no further candidates: hand off to the user/another provider (limit_handoff.py)"}
    return {"escalate": True, "next": path[0], "note": "re-check budget gate before executing"}
