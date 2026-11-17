"""Usage dashboard (text + self-contained HTML). Paid, subscription, free-tier, local and credit usage are shown in SEPARATE
sections and never summed; measured and estimated cost are labelled."""
from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

import core  # noqa: F401
from aos_lib import state_dir
from core.budget import usage_monitor as um
from core.cache import context_cache as cc

TITLES = {"paid_api": "Paid API tokens (USD)", "subscription": "Subscription allowance usage (tokens only; allowance not convertible to USD)",
          "free_tier": "Free-tier API usage (provider quota, not money)", "local": "Local inference (tokens + machine resources)", "credits": "Provider credits"}


def build(root: Path, mode: str | None = None) -> dict:
    rows = um.entries(root)
    sections = {}
    for kind in um.KINDS:
        rs = [r for r in rows if r["kind"] == kind]
        by = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_actual": 0.0, "cost_estimated": 0.0,
                                  "unpriced": 0, "retries": 0, "ok": 0, "judged": 0, "latency": [], "peak_ram_gb": 0.0, "peak_gpu_gb": 0.0})
        for r in rs:
            b = by[(r["provider"], r["model"])]
            b["calls"] += 1
            for k in ("input_tokens", "output_tokens", "cached_tokens", "retries"):
                b[k] += r[k]
            if r["cost_usd_actual"] is not None:
                b["cost_actual"] += r["cost_usd_actual"]
            elif r["cost_usd_estimated"] is not None:
                b["cost_estimated"] += r["cost_usd_estimated"]
            else:
                b["unpriced"] += 1
            if r.get("success") is not None:
                b["judged"] += 1
                b["ok"] += 1 if r["success"] else 0
            if r.get("latency_s"):
                b["latency"].append(r["latency_s"])
            res = r.get("resources") or {}
            b["peak_ram_gb"] = max(b["peak_ram_gb"], res.get("ram_used_gb", 0) or 0)
            b["peak_gpu_gb"] = max(b["peak_gpu_gb"], res.get("gpu_mem_used_gb", 0) or 0)
        table = [{"provider": p, "model": m, "calls": v["calls"], "input_tokens": v["input_tokens"], "output_tokens": v["output_tokens"], "cached_tokens": v["cached_tokens"],
                  "cost_measured_usd": round(v["cost_actual"], 6) if kind in ("paid_api", "credits") else None,
                  "cost_estimated_usd": round(v["cost_estimated"], 6) if kind in ("paid_api", "credits") else None, "unpriced_calls": v["unpriced"],
                  "retries": v["retries"], "success_rate": round(v["ok"] / v["judged"], 3) if v["judged"] else None,
                  "mean_latency_s": round(sum(v["latency"]) / len(v["latency"]), 2) if v["latency"] else None,
                  "peak_ram_gb": v["peak_ram_gb"] or None, "peak_gpu_gb": v["peak_gpu_gb"] or None} for (p, m), v in sorted(by.items())]
        sections[kind] = {"title": TITLES[kind], "rows": table, "calls": len(rs),
                          "tokens_total": sum(r["input_tokens"] + r["output_tokens"] for r in rs)}
    sp = um.spend(root, "paid_api")
    c = um.ceilings(root, mode)
    esc = [{"ts": r["ts"], "task": r["task"], "from": r["escalated_from"], "to": f"{r['provider']}/{r['model']}", "success": r.get("success")} for r in rows if r.get("escalated_from")]
    return {"sections": sections, "paid_spend": sp, "ceilings": c,
            "budget_remaining": {"day_usd": round(c["daily_usd"] - sp["day_usd"], 4), "month_usd": round(c["monthly_usd"] - sp["month_usd"], 4)},
            "escalations": esc, "cache": cc.stats(root), "tasks_judged": sum(1 for r in rows if r.get("success") is not None),
            "task_success_rate": (lambda j: round(sum(1 for r in j if r["success"]) / len(j), 3) if j else None)([r for r in rows if r.get("success") is not None]),
            "note": "Sections are different quantities and are intentionally not added together."}


def render_text(d: dict) -> str:
    o = ["AOS usage dashboard", "=" * 19]
    for k, s in d["sections"].items():
        o.append(f"\n[{s['title']}]  calls={s['calls']} tokens={s['tokens_total']}")
        for r in s["rows"]:
            o.append(f"  {r['provider']}/{r['model']}: calls={r['calls']} in={r['input_tokens']} out={r['output_tokens']} cached={r['cached_tokens']}"
                     + (f" cost measured=${r['cost_measured_usd']} estimated=${r['cost_estimated_usd']} unpriced={r['unpriced_calls']}" if r["cost_measured_usd"] is not None else "")
                     + f" retries={r['retries']} success={r['success_rate']}" + (f" peakRAM={r['peak_ram_gb']}GB" if r["peak_ram_gb"] else "") + (f" peakGPU={r['peak_gpu_gb']}GB" if r["peak_gpu_gb"] else ""))
    sp, c, br = d["paid_spend"], d["ceilings"], d["budget_remaining"]
    o.append(f"\nPaid API spend: day ${sp['day_usd']} month ${sp['month_usd']} (estimated share {sp['estimated_share']:.0%}); ceilings day ${c['daily_usd']} month ${c['monthly_usd']}; remaining day ${br['day_usd']} month ${br['month_usd']}")
    o.append(f"Task success rate: {d['task_success_rate']} over {d['tasks_judged']} judged tasks | cache: {d['cache']}")
    o.append("Escalations: " + ("; ".join(f"{e['task']}: {e['from']} -> {e['to']}" for e in d["escalations"]) or "none"))
    o.append(d["note"])
    return "\n".join(o)


def render_html(d: dict) -> str:
    e = html.escape
    parts = ["<!doctype html><meta charset=utf-8><title>AOS usage</title><style>body{font:14px system-ui;margin:24px;max-width:1100px}table{border-collapse:collapse;width:100%;margin:8px 0 24px}"
             "td,th{border:1px solid #ccc;padding:4px 8px;text-align:right}th{background:#f3f3f3}td:first-child,th:first-child{text-align:left}h2{margin:20px 0 4px;font-size:16px}.n{color:#666}</style>",
             "<h1>AOS usage dashboard</h1>", f"<p class=n>{e(d['note'])}</p>"]
    for k, s in d["sections"].items():
        parts.append(f"<h2>{e(s['title'])}</h2><p class=n>calls {s['calls']}, tokens {s['tokens_total']}</p>")
        if s["rows"]:
            cols = ["provider", "model", "calls", "input_tokens", "output_tokens", "cached_tokens", "cost_measured_usd", "cost_estimated_usd", "unpriced_calls", "retries", "success_rate", "mean_latency_s", "peak_ram_gb", "peak_gpu_gb"]
            parts.append("<table><tr>" + "".join(f"<th>{e(c)}</th>" for c in cols) + "</tr>" + "".join("<tr>" + "".join(f"<td>{e(str(r[c])) if r[c] is not None else '-'}</td>" for c in cols) + "</tr>" for r in s["rows"]) + "</table>")
    sp, br = d["paid_spend"], d["budget_remaining"]
    parts.append(f"<h2>Budget (paid API only)</h2><p>Spent today ${sp['day_usd']}, this month ${sp['month_usd']} ({sp['estimated_share']:.0%} estimated). Remaining today ${br['day_usd']}, month ${br['month_usd']}.</p>")
    parts.append(f"<h2>Task success and escalation</h2><p>Success rate {e(str(d['task_success_rate']))} over {d['tasks_judged']} judged tasks.</p><ul>" + "".join(f"<li>{e(x['ts'])} {e(str(x['task']))}: {e(str(x['from']))} &rarr; {e(x['to'])}</li>" for x in d["escalations"]) + "</ul>")
    parts.append(f"<h2>Cache</h2><pre>{e(json.dumps(d['cache']))}</pre>")
    return "".join(parts)


def write(root: Path, mode: str | None = None) -> Path:
    d = build(root, mode)
    p = state_dir(root) / "dashboard.html"
    p.write_text(render_html(d), encoding="utf-8")
    return p
