"""Usage ledger + budget guard. Paid, subscription, free-tier, local and credit usage are kept in SEPARATE buckets.

Ledger: .aos/usage/ledger.jsonl (append-only). Authorizations: .aos/usage/authorizations.jsonl (human decisions).
Cost fields: cost_usd_actual (provider-reported/measured) vs cost_usd_estimated (computed from configured prices);
`cost_basis` says which. Nothing is invented: missing price => cost None, not 0.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import core  # noqa: F401  (sys.path)
from aos_lib import append_jsonl, load_config, now_iso, read_jsonl, state_dir

KINDS = ("paid_api", "subscription", "free_tier", "local", "credits")
_KIND_ALIASES = {"api_paid": "paid_api", "api_free": "free_tier"}


def norm_kind(k: str) -> str:
    k = _KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise ValueError(f"unknown usage kind {k!r}; use one of {KINDS}")
    return k


def ledger_path(root: Path) -> Path:
    return state_dir(root) / "usage" / "ledger.jsonl"


def estimate_cost(price: dict | None, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float | None:
    """USD from a configured price_per_mtok {input, output, cache_read}; None when price unknown."""
    if not isinstance(price, dict) or "input" not in price or "output" not in price:
        return None
    fresh = max(0, input_tokens - cached_tokens)
    return round((fresh * price["input"] + cached_tokens * price.get("cache_read", price["input"]) + output_tokens * price["output"]) / 1e6, 6)


def record(root: Path, *, kind: str, provider: str, model: str, task: str, input_tokens: int = 0, output_tokens: int = 0,
           cached_tokens: int = 0, cost_usd_actual: float | None = None, cost_usd_estimated: float | None = None,
           success: bool | None = None, retries: int = 0, category: str | None = None, escalated_from: str | None = None,
           latency_s: float | None = None, resources: dict | None = None, ts: str | None = None) -> dict:
    kind = norm_kind(kind)
    if kind == "local" and (cost_usd_actual or 0) > 0:
        raise ValueError("local usage has no provider cost; put electricity etc. in cost_usd_estimated")
    rec = {"ts": ts or now_iso(), "kind": kind, "provider": provider, "model": model, "task": task, "category": category,
           "input_tokens": int(input_tokens), "output_tokens": int(output_tokens), "cached_tokens": int(cached_tokens),
           "cost_usd_actual": cost_usd_actual, "cost_usd_estimated": cost_usd_estimated,
           "cost_basis": "measured" if cost_usd_actual is not None else ("estimated" if cost_usd_estimated is not None else "none"),
           "success": success, "retries": int(retries), "escalated_from": escalated_from, "latency_s": latency_s, "resources": resources or {}}
    append_jsonl(ledger_path(root), rec)
    return rec


def entries(root: Path) -> list[dict]:
    return read_jsonl(ledger_path(root))


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def spend(root: Path, kind: str = "paid_api", now: datetime | None = None) -> dict:
    """USD spent in the current UTC day / month for one kind. Uses measured cost, else estimate, and says which."""
    now = now or datetime.now(timezone.utc)
    out = {"day_usd": 0.0, "month_usd": 0.0, "all_usd": 0.0, "unpriced_entries": 0, "estimated_share": 0.0}
    est = tot = 0.0
    for e in entries(root):
        if e["kind"] != kind:
            continue
        c = e["cost_usd_actual"] if e["cost_usd_actual"] is not None else e["cost_usd_estimated"]
        if c is None:
            out["unpriced_entries"] += 1
            continue
        t = _parse(e["ts"])
        out["all_usd"] += c
        tot += c
        est += c if e["cost_usd_actual"] is None else 0
        if (t.year, t.month) == (now.year, now.month):
            out["month_usd"] += c
            if t.day == now.day:
                out["day_usd"] += c
    out["estimated_share"] = round(est / tot, 3) if tot else 0.0
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}


def ceilings(root: Path, mode: str | None = None) -> dict:
    b = load_config("budgets", root)
    base = dict(b.get("paid_api") or {})
    over = (b.get("modes") or {}).get(mode or "", {}) or {}
    out = {}
    for k in ("per_task_usd", "daily_usd", "monthly_usd"):
        vals = [v for v in (base.get(k), over.get(k)) if v is not None]
        out[k] = min(vals) if vals else 0
    out["warn_at_fraction"] = base.get("warn_at_fraction", 0.8)
    return out


def authorizations(root: Path) -> list[dict]:
    return read_jsonl(state_dir(root) / "usage" / "authorizations.jsonl")


def authorize(root: Path, usd: float, scope: str, reason: str, expires: str | None = None) -> dict:
    """Record a HUMAN spending authorization (extra USD on top of ceilings for scope=task|day|month). Agents must not call this unprompted."""
    if usd <= 0 or scope not in ("task", "day", "month"):
        raise ValueError("usd must be > 0 and scope task|day|month")
    rec = {"ts": now_iso(), "usd": usd, "scope": scope, "reason": reason, "expires": expires, "used": 0.0}
    append_jsonl(state_dir(root) / "usage" / "authorizations.jsonl", rec)
    return rec


def _extra(root: Path, scope: str, now: datetime) -> float:
    tot = 0.0
    for a in authorizations(root):
        if a["scope"] != scope:
            continue
        if a.get("expires") and a["expires"] < now.strftime("%Y-%m-%d"):
            continue
        t = _parse(a["ts"])
        if scope == "month" and (t.year, t.month) != (now.year, now.month):
            continue
        if scope == "day" and (t.year, t.month, t.day) != (now.year, now.month, now.day):
            continue
        tot += a["usd"]
    return tot


def check(root: Path, kind: str, est_cost_usd: float | None, mode: str | None = None, now: datetime | None = None) -> dict:
    """Decide whether an action may proceed. Paid API with unknown price or over ceiling => needs_authorization (never silent)."""
    kind = norm_kind(kind)
    now = now or datetime.now(timezone.utc)
    if kind != "paid_api":
        return {"decision": "allow", "reasons": [f"{kind} usage does not incur provider charges (tracked separately)"], "remaining": None}
    c = ceilings(root, mode)
    s = spend(root, "paid_api", now)
    lim = {"task": c["per_task_usd"] + _extra(root, "task", now), "day": c["daily_usd"] + _extra(root, "day", now),
           "month": c["monthly_usd"] + _extra(root, "month", now)}
    rem = {"day": round(lim["day"] - s["day_usd"], 6), "month": round(lim["month"] - s["month_usd"], 6)}
    reasons = []
    if est_cost_usd is None:
        return {"decision": "needs_authorization", "reasons": ["price unknown: cannot prove the cost is within budget"], "remaining": rem, "limits": lim}
    if est_cost_usd > lim["task"]:
        reasons.append(f"estimated ${est_cost_usd:.4f} exceeds per-task ceiling ${lim['task']:.4f}")
    if s["day_usd"] + est_cost_usd > lim["day"]:
        reasons.append(f"would exceed daily ceiling ${lim['day']:.2f} (spent ${s['day_usd']:.4f})")
    if s["month_usd"] + est_cost_usd > lim["month"]:
        reasons.append(f"would exceed monthly ceiling ${lim['month']:.2f} (spent ${s['month_usd']:.4f})")
    if reasons:
        return {"decision": "needs_authorization", "reasons": reasons, "remaining": rem, "limits": lim}
    warn = []
    for k, spent in (("day", s["day_usd"]), ("month", s["month_usd"])):
        if lim[k] and (spent + est_cost_usd) / lim[k] >= c["warn_at_fraction"]:
            warn.append(f"{k} budget >= {int(c['warn_at_fraction'] * 100)}% used after this call")
    return {"decision": "allow", "reasons": warn or ["within ceilings"], "remaining": rem, "limits": lim}


def success_stats(root: Path, model: str, category: str | None = None) -> dict:
    rows = [e for e in entries(root) if e["model"] == model and e.get("success") is not None and (category is None or e.get("category") == category)]
    n = len(rows)
    return {"n": n, "success_rate": (sum(1 for e in rows if e["success"]) / n) if n else None,
            "mean_latency_s": (sum(e["latency_s"] for e in rows if e.get("latency_s")) / max(1, sum(1 for e in rows if e.get("latency_s")))) if rows and any(e.get("latency_s") for e in rows) else None}
