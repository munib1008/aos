#!/usr/bin/env python3
"""Task complexity scoring + model/effort routing.

  route_task.py "fix typo in login label"
  route_task.py "add RLS policy for tenants" --scores '{"security":3}' --failures 2 --json
  route_task.py "..." --available claude-sonnet-5-5,claude-haiku-5-5 --current-model claude-sonnet-5-5

Scores come from keyword heuristics (labelled as such) unless overridden with --scores.
The router RECOMMENDS; it never switches the model itself.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from aos_lib import find_root, load_config

# dimension -> [(score, regex)]; highest matching score wins.
HEURISTICS = {
    "scope": [(2, r"\b(feature|endpoint|module|workflow|engine|integration|dashboard|multi)"), (1, r"\b(fix|bug|update|change|add|component|query|formula)")],
    "architecture": [(3, r"\b(redesign|re-?architect|rewrite|migrate (the )?(framework|platform))"), (2, r"\b(refactor|architecture|multi-?module|state machine|async)")],
    "security": [(3, r"\b(cross-?tenant|tenant isolation|payment|credential|encrypt|crypto|privilege|vulnerab|exploit)"), (2, r"\b(auth|oauth|permission|rls|token|password|secret|csrf|xss|injection|session)")],
    "data_integrity": [(3, r"\b(production data|drop table|truncate|ledger|backfill|data loss|destructive)"), (2, r"\b(migration|schema|delete|transaction|concurren|race condition|idempoten)")],
    "interacting_systems": [(2, r"\b(webhook|queue|supabase|database|api|sap|stripe|e-?commerce|ci/?cd|integration)")],
    "business_criticality": [(3, r"\b(production|payment|trading|live order|invoice|ledger|billing|execution)"), (2, r"\b(customer|checkout|release|deploy)")],
    "uncertainty": [(2, r"\b(intermittent|flaky|sometimes|random|unknown|investigate|root cause|why does|unclear|regression)")],
    "coverage_gap": [(2, r"\b(no tests?|untested|legacy|undocumented|not covered)")],
}
TRIVIAL = re.compile(r"\b(typo|rename|label|wording|comment|whitespace|format(ting)?|readme|docs?)\b", re.I)


def score_text(task: str) -> dict:
    scores = {}
    for dim, rules in HEURISTICS.items():
        scores[dim] = max([s for s, rx in rules if re.search(rx, task, re.I)] or [0])
    if TRIVIAL.search(task) and max(scores.values()) <= 1:
        scores = {d: 0 for d in scores}
    # interacting systems: count distinct hits, capped
    hits = len(set(re.findall(HEURISTICS["interacting_systems"][0][1], task, re.I)))
    scores["interacting_systems"] = min(3, hits)
    return scores


def level_for(scores: dict, cfg: dict) -> tuple[int, int, list[str]]:
    total = sum(scores.values())
    c = cfg["complexity"]
    level = 1 if total <= c["l1_max"] else 2 if total <= c["l2_max"] else 3 if total <= c["l3_max"] else 4
    reasons = [f"total score {total}/24 -> level {level}"]
    for dim, floor in (c.get("floor_overrides") or {}).items():
        if scores.get(dim, 0) >= 3 and level < floor:
            level = floor
            reasons.append(f"override: {dim}=3 forces level >= {floor}")
    for dim, floor in (c.get("floor_at_score_2") or {}).items():
        if scores.get(dim, 0) >= 2 and level < floor:
            level = floor
            reasons.append(f"override: {dim}>=2 forces level >= {floor}")
    return level, total, reasons


def escalate(level: int, failures: int, cfg: dict) -> tuple[int, str | None]:
    e = cfg["escalation"]
    steps = failures // e["failures_before_escalate"]
    new = min(e["max_level"], level + steps)
    note = f"escalated {level}->{new} after {failures} evidence-based failures" if new != level else None
    return new, note


def pick_model(level: int, models_cfg: dict, available: list[str]) -> dict:
    lv = models_cfg["levels"][level]
    tier_order = ["small", "balanced", "strong", "strongest"]
    tiers = [lv["tier"]] + [t for t in tier_order[tier_order.index(lv["tier"]) + 1:]] + \
            [t for t in reversed(tier_order[:tier_order.index(lv["tier"])])]
    note = None
    for i, tier in enumerate(tiers):
        for m in models_cfg["tier_preference"].get(tier, []):
            if m in available:
                if i:
                    note = f"preferred tier '{lv['tier']}' unavailable; using '{tier}'"
                return {"model": m, "tier": tier, "effort": lv["effort"], "note": note}
    return {"model": None, "tier": lv["tier"], "effort": lv["effort"], "note": "no configured model is available"}


def route(task: str, scores_override: dict | None = None, failures: int = 0, available: list[str] | None = None,
          current_model: str | None = None, root=None) -> dict:
    opt = load_config("optimization", root)
    mod = load_config("models", root)
    heuristic = scores_override is None or len(scores_override) < len(HEURISTICS)
    scores = score_text(task)
    if scores_override:
        scores.update({k: max(0, min(3, int(v))) for k, v in scores_override.items() if k in HEURISTICS})
    level, total, reasons = level_for(scores, opt)
    base_level = level
    level, esc = escalate(level, failures, opt)
    if esc:
        reasons.append(esc)
    avail = available or mod["available"]
    choice = pick_model(level, {**mod, "levels": {int(k): v for k, v in mod["levels"].items()}}, avail)
    switch = bool(mod.get("can_switch_model_programmatically"))
    action = "none"
    if choice["model"] and current_model and current_model != choice["model"]:
        action = ("switch automatically" if switch else
                  f"recommend user selects {choice['model']} (client switching not assumed); continue with {current_model} meanwhile")
    provider_note = None
    if failures >= opt["escalation"]["switch_provider_after_evidence_based_failures"]:
        provider_note = "Repeated evidence-based failures: consider /aos-handoff to another provider (user decision)."
    ctx_budget = opt["context"]["budget_tokens"][level]
    return {
        "level": level, "base_level": base_level, "total_score": total, "scores": scores,
        "scores_are_heuristic": heuristic, "reasons": reasons, "model": choice["model"], "tier": choice["tier"],
        "effort": choice["effort"], "model_note": choice["note"], "action": action,
        "context_budget_tokens_est": ctx_budget, "max_files": opt["context"]["max_files"][level],
        "provider_note": provider_note,
        "requires_human_approval": level == 4,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task")
    ap.add_argument("--scores", help='JSON dimension overrides, e.g. {"security":3}')
    ap.add_argument("--failures", type=int, default=0, help="failed attempts that each added NEW evidence")
    ap.add_argument("--available", help="comma-separated model ids (default: config)")
    ap.add_argument("--current-model")
    ap.add_argument("--root")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = route(a.task, json.loads(a.scores) if a.scores else None, a.failures,
              a.available.split(",") if a.available else None, a.current_model, find_root(a.root))
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        h = " (heuristic)" if r["scores_are_heuristic"] else ""
        print(f"Level {r['level']} | model {r['model']} | effort {r['effort']} | ctx budget ~{r['context_budget_tokens_est']} tok est{h}")
        print("scores:", ", ".join(f"{k}={v}" for k, v in r["scores"].items()))
        for x in r["reasons"] + [r["model_note"], r["provider_note"]]:
            if x:
                print("-", x)
        if r["action"] != "none":
            print("action:", r["action"])
        if r["requires_human_approval"]:
            print("! Level 4: human approval required before high-risk deployment/destructive steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
