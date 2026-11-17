"""Model-quality benchmark: run the same deterministic tasks against local / low-cost cloud / strong cloud targets and report
correctness, completion, latency, tokens, retries and COST PER SUCCESSFUL TASK. Graders are deterministic (no LLM judge).

Targets are OpenAI-compatible endpoints (Ollama, llama.cpp server, vLLM, most cloud gateways). Anthropic/Codex subscription
sessions cannot be called from here: run the suite through your harness and `import-results` the measurements instead.
SECURITY: the python_tests grader executes MODEL-GENERATED code in a subprocess (isolated mode, temp dir, empty env, CPU/memory
limits, timeout). That is NOT a sandbox against hostile code and has no network isolation: use it only with models you
trust to be non-adversarial, preferably inside a container/VM.
Nothing is estimated silently: tokens fall back to chars/4 and are flagged `tokens_estimated`; cost is None without a price.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import core  # noqa: F401
from aos_lib import est_tokens, find_root, now_iso, state_dir
from core.budget import usage_monitor as um
from core.compute.local_model_adapter import LocalAdapterError, OpenAICompatClient

SUITE = Path(__file__).with_name("suite_basic.json")


def load_suite(path: Path | None = None) -> dict:
    return json.loads((path or SUITE).read_text(encoding="utf-8"))


def extract_code(text: str) -> str:
    m = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return (m[0] if m else text).strip()


def _limits():  # pragma: no cover - runs in the child
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 2**20, 768 * 2**20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2**20, 2**20))
    except (ImportError, ValueError, OSError):
        pass


def run_python_tests(code: str, tests: str, timeout: int = 10) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as d:
        Path(d, "t.py").write_text(code + "\n\n" + tests + "\n", encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "-I", "t.py"], cwd=d, capture_output=True, text=True, timeout=timeout, env={"PATH": "/usr/bin:/bin"},
                               preexec_fn=_limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]


def grade(task: dict, output: str) -> tuple[bool, str]:
    g = task["grader"]
    t = g["type"]
    o = output.strip()
    if t == "regex":
        return bool(re.search(g["pattern"], o, re.I if "i" in g.get("flags", "") else 0)), "regex"
    if t == "contains_all":
        low = o.lower() if g.get("case_insensitive") else o
        miss = [x for x in g["terms"] if (x.lower() if g.get("case_insensitive") else x) not in low]
        return not miss, f"missing {miss}" if miss else "ok"
    if t == "contains_any":
        low = o.lower() if g.get("case_insensitive") else o
        return any((x.lower() if g.get("case_insensitive") else x) in low for x in g["terms"]), "contains_any"
    if t == "json_fields":
        m = re.search(r"\{.*\}", o, re.S)
        try:
            j = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            j = None
        if not isinstance(j, dict):
            return False, "not JSON"
        bad = {k: j.get(k) for k, v in g["fields"].items() if str(j.get(k)) != str(v)}
        return not bad, f"wrong fields {bad}" if bad else "ok"
    if t == "python_tests":
        return run_python_tests(extract_code(o), g["tests"])
    raise ValueError(f"unknown grader {t}")


def run_target(root: Path | None, target: dict, suite: dict, repeats: int = 1, max_attempts: int = 2, client=None, record: bool = True) -> list[dict]:
    """target: {name, kind, base_url, model, tier?, price_per_mtok?, api_key_env?}"""
    client = client or OpenAICompatClient(target["base_url"], target["model"], os.environ.get(target.get("api_key_env", ""), None),
                                          allow_remote=target["kind"] != "local")
    rows = []
    for task in suite["tasks"]:
        for rep in range(repeats):
            prompt, ok, attempts, in_t, out_t, lat, est_flag, note = task["prompt"], False, 0, 0, 0, 0.0, False, ""
            for attempts in range(1, max_attempts + 1):
                try:
                    r = client.chat(prompt=prompt, max_tokens=700, temperature=0)
                except LocalAdapterError as e:
                    note = f"call failed: {e}"
                    break
                lat += r["latency_s"]
                if r["usage_reported"] and r["input_tokens"] is not None:
                    in_t += r["input_tokens"]; out_t += r["output_tokens"] or 0
                else:
                    in_t += est_tokens(prompt); out_t += est_tokens(r["text"]); est_flag = True
                ok, note = grade(task, r["text"])
                if ok:
                    break
                prompt = task["prompt"] + f"\n\nYour previous answer failed this check: {note}. Correct it."   # retry carries NEW evidence
            cost = um.estimate_cost(target.get("price_per_mtok"), in_t, out_t)
            row = {"ts": now_iso(), "target": target["name"], "kind": target["kind"], "model": target["model"], "task": task["id"], "category": task["category"],
                   "level": task["level"], "success": ok, "first_try": ok and attempts == 1, "attempts": attempts, "latency_s": round(lat, 3), "input_tokens": in_t,
                   "output_tokens": out_t, "tokens_estimated": est_flag, "cost_usd_estimated": cost, "note": note}
            rows.append(row)
            if record and root:
                um.record(root, kind=target["kind"], provider=target["name"], model=target["model"], task=f"bench:{task['id']}", input_tokens=in_t, output_tokens=out_t,
                          cost_usd_estimated=cost, success=ok, retries=attempts - 1, category=task["category"], latency_s=round(lat, 3))
    if record and root:
        p = state_dir(root) / "benchmarks" / "model_quality.jsonl"
        p.parent.mkdir(exist_ok=True)
        with p.open("a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for name in sorted({r["target"] for r in rows}):
        rs = [r for r in rows if r["target"] == name]
        n, ok = len(rs), [r for r in rs if r["success"]]
        costs = [r["cost_usd_estimated"] for r in rs]
        total_cost = None if any(c is None for c in costs) else round(sum(costs), 6)
        out.append({"target": name, "kind": rs[0]["kind"], "model": rs[0]["model"], "tasks": n, "completion_rate": round(len(ok) / n, 3),
                    "first_try_rate": round(sum(r["first_try"] for r in rs) / n, 3), "mean_attempts": round(statistics.mean(r["attempts"] for r in rs), 2),
                    "mean_latency_s": round(statistics.mean(r["latency_s"] for r in rs), 2), "input_tokens": sum(r["input_tokens"] for r in rs),
                    "output_tokens": sum(r["output_tokens"] for r in rs), "tokens_estimated_any": any(r["tokens_estimated"] for r in rs),
                    "total_cost_usd": total_cost, "cost_per_success_usd": (round(total_cost / len(ok), 6) if total_cost is not None and ok else None),
                    "tokens_per_success": round((sum(r["input_tokens"] + r["output_tokens"] for r in rs)) / len(ok)) if ok else None,
                    "cost_note": "no price configured: cost unknown (not zero)" if total_cost is None else ("local/free: no provider charge" if rs[0]["kind"] in ("local", "free_tier") else "")})
    return out


def import_results(path: Path) -> list[dict]:
    """Load measurements produced elsewhere (e.g. by running the suite through Claude Code/Codex). Same row schema as run_target."""
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def by_category(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        c = out.setdefault(r["target"], {}).setdefault(r["category"], {"n": 0, "ok": 0})
        c["n"] += 1
        c["ok"] += 1 if r["success"] else 0
    return out


def report(rows: list[dict], threshold: float = 0.8, min_n: int = 3) -> str:
    """Markdown comparison + verdict. Verdicts only use measured rows; thin samples are called out, unknown prices stay unknown."""
    summ = summarize(rows)
    cats = by_category(rows)
    L = ["# Model-quality comparison", "", f"Tasks per target: {summ[0]['tasks'] if summ else 0}. Graders are deterministic; python_tests executes model code in a subprocess (not a sandbox).", "",
         "| Target | Kind | Completion | First try | Attempts | Latency s | Tokens/success | Cost/success USD | Note |", "|---|---|---|---|---|---|---|---|---|"]
    for s_ in summ:
        cps = s_["cost_per_success_usd"]
        L.append(f"| {s_['target']} | {s_['kind']} | {s_['completion_rate']:.0%} | {s_['first_try_rate']:.0%} | {s_['mean_attempts']} | {s_['mean_latency_s']} | {s_['tokens_per_success']}{'*' if s_['tokens_estimated_any'] else ''} | "
                 f"{'unknown' if cps is None and s_['kind'] not in ('local', 'free_tier') else (cps if cps is not None else 'no provider charge')} | {s_['cost_note']} |")
    L += ["", "`*` tokens estimated (endpoint did not report usage). Local electricity/wear is not counted.", "", "## Per category (completion, n)"]
    L.append("| Target | " + " | ".join(sorted({c for t in cats.values() for c in t})) + " |")
    allc = sorted({c for t in cats.values() for c in t})
    L.append("|---|" + "---|" * len(allc))
    for t, cc in cats.items():
        L.append(f"| {t} | " + " | ".join(f"{cc[c]['ok']}/{cc[c]['n']}" if c in cc else "-" for c in allc) + " |")
    L += ["", "## Verdict (rules: completion >= %d%%, at least %d samples per category)" % (int(threshold * 100), min_n)]
    for t, cc in cats.items():
        kind = next(x["kind"] for x in summ if x["target"] == t)
        ok = sorted(c for c, v in cc.items() if v["n"] >= min_n and v["ok"] / v["n"] >= threshold)
        thin = sorted(c for c, v in cc.items() if v["n"] < min_n)
        bad = sorted(c for c, v in cc.items() if v["n"] >= min_n and v["ok"] / v["n"] < threshold)
        L.append(f"- **{t}** ({kind}): reliable for {ok or 'nothing yet'}; below threshold: {bad or 'none'}; too few samples: {thin or 'none'}.")
    loc = [x for x in summ if x["kind"] == "local"]
    cloud = [x for x in summ if x["kind"] != "local"]
    if loc and cloud:
        lo = {c for c, v in cats[loc[0]["target"]].items() if v["n"] >= min_n and v["ok"] / v["n"] >= threshold}
        L.append("")
        L.append(f"**Offload candidates** (local reliable, verify outputs with tests): {sorted(lo) or 'none'}. Everything else stays on the cloud/subscription model.")
        priced = [x for x in cloud if x["cost_per_success_usd"] is not None]
        if priced:
            L.append(f"Cheapest priced cloud target per success: {min(priced, key=lambda x: x['cost_per_success_usd'])['target']}.")
        else:
            L.append("Cloud cost per success is **unknown** (no price configured): set `price_per_mtok` to compare money; token counts are shown above.")
    else:
        L.append("\nNeed at least one local and one non-local target to compare.")
    return "\n".join(L) + "\n"


def doctor(base_url: str, model: str | None = None) -> dict:
    """First-run check for a local endpoint: reachable? which models? can it answer one trivial prompt?"""
    out = {"base_url": base_url, "reachable": False, "models": [], "smoke": None, "advice": []}
    try:
        c = OpenAICompatClient(base_url, model)
        out["models"] = c.list_models()
        out["reachable"] = True
    except LocalAdapterError as e:
        out["advice"] += [f"cannot reach {base_url}: {e}", "start your engine (e.g. `ollama serve`, or `llama-server -m model.gguf --port 8080`) and pass its /v1 URL"]
        return out
    if not out["models"]:
        out["advice"].append("no models served: e.g. `ollama pull <model>` (pick a size from `tccm.py inventory`; verify the licence)")
        return out
    m = model or out["models"][0]
    try:
        r = OpenAICompatClient(base_url, m).chat("Reply with the single word: pong", max_tokens=16)
        out["smoke"] = {"model": m, "latency_s": r["latency_s"], "reply": r["text"][:60], "usage_reported": r["usage_reported"]}
    except LocalAdapterError as e:
        out["advice"].append(f"model {m} failed a trivial prompt: {e}")
    return out
