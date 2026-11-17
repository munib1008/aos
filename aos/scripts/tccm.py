#!/usr/bin/env python3
"""Token Capacity & Compute Manager CLI (AOS 3.0).

  tccm.py inventory [--no-probe]                 hardware + engines + providers + local-model recommendation
  tccm.py route "task" [--mode M] [--confidential] [--input-tokens N] [--local-model provider=model]
  tccm.py budget status|check --kind K --cost USD|record ...|authorize --usd N --scope day --reason "..."   (authorize = human decision)
  tccm.py cache stats|clear
  tccm.py dashboard [--html]
  tccm.py plan                                   capacity plan from measured data only
  tccm.py bench run --target NAME,KIND,BASE_URL,MODEL[,PRICE_IN,PRICE_OUT] [--repeats N] | summary | import FILE
  tccm.py credits                                read-only list of official programs (never applies/claims)
  tccm.py switch --provider P --reason "limit reached" | exhausted --provider P --hours H
Runs offline except for local endpoint probes (loopback) and benchmark targets you name.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aos_lib import AOS_DIR, find_root, state_dir  # noqa: F401
sys.path.insert(0, str(AOS_DIR))

from core.budget import usage_dashboard as ud, usage_monitor as um
from core.cache import context_cache as cc
from core.budget import capacity_plan as cp
from core.routing import model_selector as ms, provider_switch as ps


def feature(name: str, module: str, root: Path):
    """Import an optional TCCM module. Refuses when the active policy profile disables the feature or the build omits it."""
    from aos_lib import active_profile, config_path, parse_yaml
    prof = active_profile(root)
    if prof:
        pol = AOS_DIR / "profiles" / prof / f"{prof}-policy.yaml"
        pol = pol if pol.exists() else AOS_DIR / "config" / f"{prof}-policy.yaml"
        why = (parse_yaml(pol.read_text()).get("disabled") or {}).get(name) if pol.exists() else None
        if why:
            raise SystemExit(f"disabled by the '{prof}' policy profile ({name}): {why}")
    try:
        import importlib
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        raise SystemExit(f"feature not included in this build ({name}): {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inventory"); i.add_argument("--no-probe", action="store_true")
    r = sub.add_parser("route"); r.add_argument("task"); r.add_argument("--mode", choices=["local_first", "balanced", "quality_first"]); r.add_argument("--confidential", action="store_true")
    r.add_argument("--input-tokens", type=int); r.add_argument("--local-model", action="append", default=[]); r.add_argument("--non-commercial", action="store_true"); r.add_argument("--no-local-detect", action="store_true")
    b = sub.add_parser("budget"); b.add_argument("action", choices=["status", "check", "record", "authorize"]); b.add_argument("--kind", default="paid_api"); b.add_argument("--cost", type=float)
    b.add_argument("--mode"); b.add_argument("--usd", type=float); b.add_argument("--scope", default="day"); b.add_argument("--reason", default=""); b.add_argument("--provider"); b.add_argument("--model")
    b.add_argument("--task", default=""); b.add_argument("--input-tokens", type=int, default=0); b.add_argument("--output-tokens", type=int, default=0); b.add_argument("--cached-tokens", type=int, default=0)
    b.add_argument("--cost-actual", type=float); b.add_argument("--cost-estimated", type=float); b.add_argument("--success", choices=["true", "false"]); b.add_argument("--retries", type=int, default=0)
    b.add_argument("--category"); b.add_argument("--escalated-from")
    c = sub.add_parser("cache"); c.add_argument("action", choices=["stats", "clear"])
    d = sub.add_parser("dashboard"); d.add_argument("--html", action="store_true"); d.add_argument("--mode")
    sub.add_parser("plan"); sub.add_parser("credits")
    bn = sub.add_parser("bench"); bn.add_argument("action", choices=["run", "summary", "import"]); bn.add_argument("--target", action="append", default=[]); bn.add_argument("--repeats", type=int, default=1)
    bn.add_argument("--suite"); bn.add_argument("file", nargs="?")
    s = sub.add_parser("switch"); s.add_argument("action", nargs="?", default="plan", choices=["plan", "exhausted"]); s.add_argument("--provider", required=True); s.add_argument("--reason", default="usage limit reached"); s.add_argument("--hours", type=float)
    a = ap.parse_args(argv)
    root = find_root(a.root)
    pj = lambda o: print(json.dumps(o, indent=2, default=str))
    if a.cmd == "inventory":
        pj(feature("local_model_runtime", "core.compute.capacity_manager", root).inventory(root, probe=not a.no_probe))
    elif a.cmd == "route":
        lm = {}
        for x in a.local_model:
            k, _, v = x.partition("=")
            lm.setdefault(k, []).append(v)
        if not lm and not a.no_local_detect:
            lma = feature("local_model_runtime", "core.compute.local_model_adapter", root)
            lm = {e["engine_id"]: e["models"] for e in [{"engine_id": {"ollama": "local-ollama", "llama.cpp": "local-llamacpp", "vllm": "local-vllm"}.get(e["engine"], e["engine"]), **e} for e in lma.detect_engines()] if e["running"]}
        pj(ms.select(a.task, root=root, mode=a.mode, input_tokens=a.input_tokens, confidential=a.confidential, commercial=not a.non_commercial, local_models=lm))
    elif a.cmd == "budget":
        if a.action == "status":
            pj({"paid_spend": um.spend(root), "ceilings": um.ceilings(root, a.mode)})
        elif a.action == "check":
            pj(um.check(root, a.kind, a.cost, a.mode))
        elif a.action == "record":
            pj(um.record(root, kind=a.kind, provider=a.provider or "unknown", model=a.model or "unknown", task=a.task, input_tokens=a.input_tokens, output_tokens=a.output_tokens,
                         cached_tokens=a.cached_tokens, cost_usd_actual=a.cost_actual, cost_usd_estimated=a.cost_estimated, success=None if a.success is None else a.success == "true",
                         retries=a.retries, category=a.category, escalated_from=a.escalated_from))
        else:
            pj(um.authorize(root, a.usd or 0, a.scope, a.reason))
    elif a.cmd == "cache":
        pj(cc.stats(root) if a.action == "stats" else {"cleared": cc.clear(root)})
    elif a.cmd == "dashboard":
        dd = ud.build(root, a.mode)
        print(ud.render_text(dd))
        if a.html:
            print("HTML:", ud.write(root, a.mode))
    elif a.cmd == "plan":
        pj(cp.plan(root))
    elif a.cmd == "credits":
        pj(feature("credits_registry", "core.compute.credits_registry", root).list_programs(root) or "no programs registered (config/credits.yaml); add official sources with last_checked dates")
    elif a.cmd == "bench":
        mq = feature("local_model_runtime", "core.benchmark.model_quality", root)
        qp = state_dir(root) / "benchmarks" / "model_quality.jsonl"
        if a.action == "run":
            suite = mq.load_suite(Path(a.suite) if a.suite else None)
            rows = []
            for t in a.target:
                parts = t.split(",")
                tg = {"name": parts[0], "kind": parts[1], "base_url": parts[2], "model": parts[3]}
                if len(parts) >= 6:
                    tg["price_per_mtok"] = {"input": float(parts[4]), "output": float(parts[5])}
                rows += mq.run_target(root, tg, suite, a.repeats)
            pj(mq.summarize(rows))
        elif a.action == "import":
            rows = mq.import_results(Path(a.file))
            qp.parent.mkdir(exist_ok=True)
            with qp.open("a") as f:
                f.writelines(json.dumps(x) + "\n" for x in rows)
            pj(mq.summarize(rows))
        else:
            rows = mq.import_results(qp) if qp.exists() else []
            pj(mq.summarize(rows) if rows else "no benchmark results yet")
    else:
        if a.action == "exhausted":
            pj(ps.mark_exhausted(root, a.provider, a.hours, a.reason))
        else:
            pj(ps.plan_switch(root, a.provider, a.reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
