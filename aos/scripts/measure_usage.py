#!/usr/bin/env python3
"""Measure real usage; compare baseline vs AOS runs. Never invents numbers.

  measure_usage.py transcript FILE.jsonl            # tokens/tool calls from a Claude Code transcript
  measure_usage.py transcript --latest              # newest transcript for this project in ~/.claude/projects
  measure_usage.py record --task B-localized-bug --variant baseline|aos --source transcript|api|manual \
        --input-tokens N --output-tokens N [--cache-read-tokens N] [--model-calls N] [--tool-calls N] \
        [--seconds S] [--retries N] [--passed true|false] [--regressions N] [--human-corrections N] [--cost USD]
  measure_usage.py compare [--min-runs 3]

Cost is computed only when models.yaml has price_per_mtok {input,output,cache_read} for the model; else "n/a".
Savings are reported only when both variants exist for a task, and are rejected if quality dropped.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from aos_lib import append_jsonl, find_root, load_config, now_iso, read_jsonl, state_dir


def parse_transcript(path: Path) -> dict:
    """Dedupe by message id (Claude Code can log one API message across several lines; last wins)."""
    msgs: dict[str, dict] = {}
    tools = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = d.get("message")
        if not isinstance(m, dict) or d.get("type") != "assistant":
            continue
        u = m.get("usage")
        if isinstance(u, dict):
            msgs[m.get("id") or d.get("uuid") or str(len(msgs))] = {"u": u, "model": m.get("model")}
        for c in (m.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "tool_use":
                tools += 1
    tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    by_model: dict[str, int] = {}
    for v in msgs.values():
        u = v["u"]
        tot["input_tokens"] += u.get("input_tokens", 0) or 0
        tot["output_tokens"] += u.get("output_tokens", 0) or 0
        tot["cache_read_tokens"] += u.get("cache_read_input_tokens", 0) or 0
        tot["cache_creation_tokens"] += u.get("cache_creation_input_tokens", 0) or 0
        by_model[v["model"] or "unknown"] = by_model.get(v["model"] or "unknown", 0) + 1
    return {**tot, "model_calls": len(msgs), "tool_calls": tools, "models": by_model, "source": "transcript"}


def cost(root: Path, model: str | None, t: dict) -> float | None:
    p = ((load_config("models", root).get("models") or {}).get(model or "") or {}).get("price_per_mtok")
    if not isinstance(p, dict) or "input" not in p or "output" not in p:
        return None
    return round((t.get("input_tokens", 0) * p["input"] + t.get("output_tokens", 0) * p["output"]
                  + t.get("cache_read_tokens", 0) * p.get("cache_read", p["input"])) / 1e6, 4)


def find_latest_transcript(root: Path) -> Path | None:
    base = Path.home() / ".claude" / "projects" / str(root).replace("/", "-")
    fs = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if base.exists() else []
    return fs[-1] if fs else None


def compare(records: list[dict], min_runs: int = 3) -> list[dict]:
    out = []
    for task in sorted({r["task"] for r in records}):
        row = {"task": task}
        per = {}
        for v in ("baseline", "aos"):
            rs = [r for r in records if r["task"] == task and r["variant"] == v]
            per[v] = rs
            row[f"n_{v}"] = len(rs)
        if not per["baseline"] or not per["aos"]:
            row["verdict"] = "INCOMPLETE: need both baseline and aos runs"
            out.append(row)
            continue

        def med(rs, k):
            vals = [r[k] for r in rs if isinstance(r.get(k), (int, float))]
            return statistics.median(vals) if vals else None

        def rate(rs):
            vals = [r["passed"] for r in rs if isinstance(r.get("passed"), bool)]
            return sum(vals) / len(vals) if vals else None
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "model_calls", "tool_calls", "seconds", "retries", "regressions", "human_corrections", "cost"):
            b, a = med(per["baseline"], k), med(per["aos"], k)
            row[k] = {"baseline": b, "aos": a, "delta_pct": round((a - b) / b * 100, 1) if b and a is not None else None}
        pb, pa = rate(per["baseline"]), rate(per["aos"])
        row["pass_rate"] = {"baseline": pb, "aos": pa}
        reg_b, reg_a = row["regressions"]["baseline"] or 0, row["regressions"]["aos"] or 0
        if pb is None or pa is None:
            row["verdict"] = "UNKNOWN: pass/fail not recorded"
        elif pa < pb or reg_a > reg_b:
            row["verdict"] = "REJECT: quality gate failed (pass rate or regressions worse) - revert optimization or raise effort"
        elif min(row["n_baseline"], row["n_aos"]) < min_runs:
            row["verdict"] = f"PRELIMINARY: <{min_runs} runs per variant; not statistically meaningful"
        else:
            row["verdict"] = "ACCEPT: quality gate met"
        out.append(row)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcript"); t.add_argument("file", nargs="?"); t.add_argument("--latest", action="store_true")
    r = sub.add_parser("record")
    r.add_argument("--task", required=True); r.add_argument("--variant", required=True, choices=["baseline", "aos"])
    r.add_argument("--source", required=True, choices=["transcript", "api", "manual"])
    for k in ("input-tokens", "output-tokens", "cache-read-tokens", "model-calls", "tool-calls", "retries", "regressions", "human-corrections"):
        r.add_argument(f"--{k}", type=int)
    r.add_argument("--seconds", type=float); r.add_argument("--cost", type=float); r.add_argument("--passed", choices=["true", "false"]); r.add_argument("--model")
    c = sub.add_parser("compare"); c.add_argument("--min-runs", type=int, default=3)
    a = ap.parse_args(argv)
    root = find_root(a.root)
    mfile = state_dir(root) / "metrics.jsonl"
    if a.cmd == "transcript":
        f = Path(a.file) if a.file else find_latest_transcript(root)
        if not f or not f.exists():
            print("no transcript found (pass a file; usage data is only available where the client writes it)", file=sys.stderr)
            return 2
        res = parse_transcript(f)
        top = max(res["models"], key=res["models"].get) if res["models"] else None
        res["cost_usd"] = cost(root, top, res)
        if res["cost_usd"] is None:
            res["cost_usd"] = "n/a (no price_per_mtok configured)"
        print(json.dumps(res, indent=2))
    elif a.cmd == "record":
        rec = {"ts": now_iso(), "task": a.task, "variant": a.variant, "source": a.source, "model": a.model, "seconds": a.seconds, "cost": a.cost,
               "passed": None if a.passed is None else a.passed == "true"}
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "model_calls", "tool_calls", "retries", "regressions", "human_corrections"):
            rec[k] = getattr(a, k)
        append_jsonl(mfile, rec)
        print("recorded", a.task, a.variant)
    else:
        rows = compare(read_jsonl(mfile), a.min_runs)
        print(json.dumps(rows, indent=2) if rows else "no metrics recorded yet: run benchmarks first (docs/BENCHMARKS.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
