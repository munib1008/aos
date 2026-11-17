#!/usr/bin/env python3
"""Reproducible OFFLINE benchmark of the deterministic part of AOS: finding the right code and how much text that costs.

  benchmark_context.py run [--root REPO] [--n 30] [--seed 7] [--baseline-files 5] [--level 2] [--json]
  benchmark_context.py run --tasks tasks.json      # [{"query": "...", "expected": ["path/file.py"], "symbol": "optional"}]

Tasks are generated from the repo's own symbols (query = words of a symbol name; expected = the file defining it) unless
--tasks is given. Compared per task:
  naive : grep for query words, read the top K matching files IN FULL           (what an unguided agent often does)
  aos   : AOS index query + build_context bundle (snippets, budgeted)
Metrics: file recall (expected file found), definition coverage (the symbol's definition text is present in the context),
estimated tokens (chars/4 - an ESTIMATE), and their ratio. NOT measured here: model answer quality, retries, wall-clock model
cost. Queries contain symbol words, which helps BOTH methods; absolute numbers are optimistic, the comparison is the signal.
End-to-end cost needs real runs (docs/BENCHMARKS.md, measure_usage.py).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

from aos_lib import est_tokens, find_root, state_dir, tokenize
import index_repository as ix
import build_context as bc

SKIP_EXT = {".md", ".json", ".yml", ".yaml", ".toml", ".lock", ".txt", ".svg", ".xml"}


def gen_tasks(root: Path, idx: dict, n: int, seed: int) -> list[dict]:
    defs: dict[str, list[str]] = {}
    for rel, meta in idx["files"].items():
        if rel in idx["tests"] or meta.get("ext") in SKIP_EXT or meta.get("skipped"):
            continue
        for name, kind, line in meta.get("symbols", []):
            if len(tokenize(name)) >= 2 and len(name) >= 8:
                defs.setdefault(name, []).append(rel)
    uniq = sorted((s, f[0]) for s, f in defs.items() if len(f) == 1)
    random.Random(seed).shuffle(uniq)
    return [{"query": "fix bug in " + " ".join(tokenize(s)), "expected": [f], "symbol": s} for s, f in uniq[:n]]


def naive(root: Path, query: str, k: int) -> tuple[set[str], int, str]:
    words = sorted(set(tokenize(query)) - {"fix", "bug"})
    hits: dict[str, int] = {}
    for rel in ix.list_files(root):
        p = root / rel
        if Path(rel).suffix.lower() in ix.BINARY_EXT or p.suffix.lower() in SKIP_EXT:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        c = sum(t.count(w) for w in words)
        if c:
            hits[rel] = c
    top = sorted(hits, key=lambda r: (-hits[r], r))[:k]
    text = "".join((root / r).read_text(encoding="utf-8", errors="ignore") for r in top)
    return set(top), est_tokens(text), text


def run(root: Path, tasks: list[dict] | None, n: int, seed: int, k: int, level: int) -> dict:
    t0 = time.time()
    idx = ix.build(root)
    t_index = time.time() - t0
    tasks = tasks or gen_tasks(root, idx, n, seed)
    rows = []
    for t in tasks:
        sym = t.get("symbol")
        exp = set(t["expected"])
        nf, ntok, ntext = naive(root, t["query"], k)
        out = bc.build(root, t["query"], level=level)
        found = set(out["files"])
        row = {"query": t["query"], "expected": sorted(exp),
               "naive": {"tokens": ntok, "recall": bool(exp & nf), "has_def": bool(sym and re.search(rf"\b{re.escape(sym)}\b", ntext))},
               "aos": {"tokens": out["est_tokens"], "recall": bool(exp & found), "has_def": bool(sym and re.search(rf"\b{re.escape(sym)}\b", out["text"]))}}
        rows.append(row)

    def agg(m):
        toks = [r[m]["tokens"] for r in rows]
        return {"mean_tokens": round(statistics.mean(toks)), "median_tokens": round(statistics.median(toks)),
                "file_recall": round(sum(r[m]["recall"] for r in rows) / len(rows), 3),
                "definition_coverage": round(sum(r[m]["has_def"] for r in rows) / len(rows), 3)}
    a, b = agg("naive"), agg("aos")
    return {"repo": str(root), "tasks": len(rows), "baseline_files_read": k, "aos_level": level, "index_seconds": round(t_index, 2),
            "index_files": idx["stats"]["files"], "naive": a, "aos": b,
            "token_ratio_aos_over_naive": round(b["mean_tokens"] / a["mean_tokens"], 3) if a["mean_tokens"] else None,
            "caveats": ["tokens are chars/4 estimates", "queries contain symbol words (optimistic for both)", "no answer-quality or retry measurement"],
            "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--root"); r.add_argument("--n", type=int, default=30); r.add_argument("--seed", type=int, default=7)
    r.add_argument("--baseline-files", type=int, default=5); r.add_argument("--level", type=int, default=2, choices=[1, 2, 3, 4])
    r.add_argument("--tasks"); r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    res = run(root, json.loads(Path(a.tasks).read_text()) if a.tasks else None, a.n, a.seed, a.baseline_files, a.level)
    (state_dir(root) / "benchmarks").mkdir(exist_ok=True)
    (state_dir(root) / "benchmarks" / f"context-{int(time.time())}.json").write_text(json.dumps(res, indent=1))
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=2))
    else:
        n_, a_ = res["naive"], res["aos"]
        print(f"{res['repo']}  tasks={res['tasks']}  files indexed={res['index_files']}  index {res['index_seconds']}s")
        print(f"{'':8}{'mean tok':>10}{'median':>9}{'file recall':>13}{'def coverage':>14}")
        for name, m in (("naive", n_), ("aos", a_)):
            print(f"{name:8}{m['mean_tokens']:>10}{m['median_tokens']:>9}{m['file_recall']:>13}{m['definition_coverage']:>14}")
        print(f"AOS/naive token ratio: {res['token_ratio_aos_over_naive']}")
        print("caveats:", "; ".join(res["caveats"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
