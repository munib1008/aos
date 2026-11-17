"""Usage-limit handoff: save state, preserve git, prepare a portable continuation package, and REQUEST authorization before any
switch that changes cost. Never bypasses authentication, caps or account restrictions and never switches automatically."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core  # noqa: F401
from aos_lib import atomic_write, git_state, load_config, now_iso, state_dir
from core.budget import usage_monitor as um
from core.routing import model_selector as ms
import create_handoff
import session_manager as sm


def _ex_path(root: Path) -> Path:
    return state_dir(root) / "usage" / "exhausted.json"


def mark_exhausted(root: Path, provider: str, hours: float | None = None, note: str = "") -> dict:
    p = _ex_path(root)
    d = json.loads(p.read_text()) if p.exists() else {}
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ") if hours else None
    d[provider] = {"marked": now_iso(), "until": until, "note": note}
    atomic_write(p, json.dumps(d, indent=1))
    return d[provider]


def exhausted(root: Path) -> set[str]:
    p = _ex_path(root)
    if not p.exists():
        return set()
    now = now_iso()
    return {k for k, v in json.loads(p.read_text()).items() if not v.get("until") or v["until"] > now}


def plan_switch(root: Path, current_provider: str, reason: str, mode: str | None = None, local_models: dict | None = None, session: str | None = None) -> dict:
    mark_exhausted(root, current_provider, note=reason) if current_provider not in exhausted(root) else None
    out = create_handoff.build(root, session)                          # saves task state, git state, tests, acceptance criteria, memory
    g = git_state(root)
    gone = exhausted(root)
    cats = ms.select("continue the recorded task", root=root, mode=mode, local_models=local_models)
    cands = []
    for c in [cats.get("choice")] + list(cats.get("escalation_path") or []):
        if c and c["provider"] not in gone:
            cands.append(c)
    cur_kind = next((k["kind"] for k in ms._catalog(root, local_models) if k["provider"] == current_provider), None)
    items = []
    for c in cands:
        chk = um.check(root, c["kind"], c["est_cost_usd"], mode)
        changes_cost = (cur_kind != c["kind"]) and c["kind"] in ("paid_api", "credits")
        items.append({**c, "authorization_required": chk["decision"] != "allow" or changes_cost, "budget_gate": chk["decision"], "gate_reasons": chk["reasons"]})
    body = [f"# Switch request - {current_provider} reached its usage limit", f"Reason: {reason}", f"Created {now_iso()} | branch `{g['branch']}` | commit `{g['commit'][:12]}` | uncommitted files: {len(g['dirty_files'])}", "",
            "State preserved in this folder (HANDOFF.md, TASK_CONTEXT.json, CHANGES.md, TEST_RESULTS.md, NEXT_ACTIONS.md). Commit/stash local changes yourself if the next provider works on a different machine.", "",
            "## Candidates (best first)"]
    for i in items:
        body.append(f"- {i['provider']}/{i['model']} [{i['kind']}] p_success {i['p_success']} ({i['p_source']}), est cost {i['est_cost_usd']}, **{'AUTHORIZATION REQUIRED' if i['authorization_required'] else 'no authorization needed'}** ({'; '.join(i['gate_reasons'])})")
    if not items:
        body.append("- none eligible: wait for the allowance to reset, enable another provider in providers.yaml, or start a local engine.")
    body += ["", "## Resume", "Start the chosen provider's own supported interface (Claude Code / Codex / local agent), give it the continuation prompt from HANDOFF.md, and verify repo state and tests first.",
             "AOS does not bypass authentication, rate limits, caps or account restrictions, and does not switch providers or incur paid charges without your explicit approval."]
    atomic_write(out / "SWITCH_REQUEST.md", "\n".join(body) + "\n")
    return {"handoff_dir": str(out), "candidates": items, "needs_user_authorization": any(i["authorization_required"] for i in items), "exhausted": sorted(gone), "switched": False}
