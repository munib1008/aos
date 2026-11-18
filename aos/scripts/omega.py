#!/usr/bin/env python3
"""Project OMEGA state: goal + acceptance criteria, evidence-gated backlog ledger, scorecard, release gate, approval queue, action guard.

  omega.py init --goal "..." [--repo URL] [--project NAME] [--accept "criterion" ...]
  omega.py accept add "text" | met ID --evidence "test:pytest => PASSED"
  omega.py backlog add --title T --category C --severity critical|high|medium|low --factors '{"impact":3,...}' [--depends-on ID,..] [--module M]
  omega.py backlog update ID --status in_progress|completed|failed|blocked|needs_verification|out_of_scope [--evidence E ...] [--blocker "..."] [--note "..."]
  omega.py next                      # highest-priority dependency-ready item
  omega.py feature add --module M --name N --status COMPLETE_VERIFIED|... [--evidence E]
  omega.py score set CATEGORY N --evidence E ...  | score show
  omega.py gate [--json]             # release gate: not_ready | staging | uat | production (needs human approval)
  omega.py done [--json]             # goal met?  exit 0 = done / done_with_blockers
  omega.py approvals list|resolve ID --approve|--reject --by NAME
  omega.py guard --command "..."     # allow | queue | deny
  omega.py hook                      # Claude Code PreToolUse hook (stdin JSON); blocks guarded/denied Bash commands, exit 2
  omega.py checkpoint | report | status [--json]

Evidence strings (anything else is refused): test:<command> => PASSED | file:<path[:line]> | commit:<sha> | doc:<path> | url:https://... | run:<command> => exit 0
Nothing is "completed" without verifiable evidence; the gate re-verifies files and commits and fails on fabricated ones.
Statuses are claims about work; this tool records and checks them, it does not run the work.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from aos_lib import atomic_write, find_root, git_state, load_config, now_iso, redact, run, state_dir

STATUSES = ("not_started", "in_progress", "completed", "failed", "blocked", "needs_verification", "out_of_scope")
OPEN = ("not_started", "in_progress", "failed", "needs_verification")
FEATURE_STATUSES = ("COMPLETE_VERIFIED", "COMPLETE_UNVERIFIED", "PARTIAL", "BROKEN", "MOCK_ONLY", "MISSING", "DEPRECATED", "BLOCKED")
SEVERITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}
CATEGORIES = ["business_functionality", "incomplete_workflow", "broken_feature", "ux_consistency", "api", "data_model", "security", "performance", "accessibility",
              "integration", "automation", "tests", "deployment", "tech_debt", "monitoring", "documentation", "scalability", "error_handling", "data_integrity", "complexity"]
FACTORS = ("impact", "severity", "frequency", "user_impact", "data_risk", "security", "effort", "uncertainty", "regression_risk")


def odir(root: Path) -> Path:
    d = state_dir(root) / "omega"
    d.mkdir(exist_ok=True)
    return d


def load(root: Path, name: str, default):
    p = odir(root) / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(root: Path, name: str, obj) -> None:
    atomic_write(odir(root) / name, json.dumps(json.loads(redact(json.dumps(obj))), indent=2, sort_keys=True))


# ------------------------------------------------------------------ evidence
def check_evidence(root: Path, e: str) -> tuple[bool, str]:
    m = re.match(r"^(test|file|commit|doc|url|run):(.+)$", e.strip(), re.S)
    if not m:
        return False, "format must be kind:value (test|file|commit|doc|url|run)"
    kind, val = m.group(1), m.group(2).strip()
    if kind in ("file", "doc"):
        p = val.split(":")[0]
        if ".." in Path(p).parts or Path(p).is_absolute():
            return False, "path must be relative to the repo"
        return ((root / p).exists(), "ok" if (root / p).exists() else f"file not found: {p}")
    if kind == "commit":
        ok = bool(re.fullmatch(r"[0-9a-f]{7,40}", val)) and run(["git", "cat-file", "-e", val + "^{commit}"], cwd=root)[0] == 0
        return ok, "ok" if ok else f"commit not found: {val}"
    if kind == "url":
        return val.startswith("https://"), "ok" if val.startswith("https://") else "url must be https"
    if kind == "test":
        if "=>" not in val:
            return False, "test evidence needs '=> PASSED' (command and result)"
        res = val.split("=>")[-1].upper()
        return ("PASSED" in res and "FAILED" not in res, "ok" if "PASSED" in res and "FAILED" not in res else "test result is not PASSED")
    if "=>" not in val:
        return False, "run evidence needs '=> exit 0'"
    return ("exit 0" in val.split("=>")[-1], "ok" if "exit 0" in val.split("=>")[-1] else "run did not exit 0")


def valid_evidence(root: Path, items: list[str]) -> tuple[list[str], list[str]]:
    good, bad = [], []
    for e in items:
        ok, why = check_evidence(root, e)
        (good if ok else bad).append(e if ok else f"{e} ({why})")
    return good, bad


# ------------------------------------------------------------------ state
def init(root: Path, goal: str, repo: str = "", project: str = "", accept: list[str] | None = None) -> dict:
    st = {"project": project or root.name, "repo": repo, "goal": goal, "created": now_iso(), "status": "running", "phase": "audit",
          "acceptance": [{"id": f"A{i}", "text": t, "status": "open", "evidence": []} for i, t in enumerate(accept or [], 1)], "features": [], "baseline": git_state(root)}
    save(root, "state.json", st)
    for f, d in (("backlog.json", {}), ("scorecard.json", {}), ("approvals.json", [])):
        if not (odir(root) / f).exists():
            save(root, f, d)
    return st


def priority(factors: dict, unblocks: int, cfg: dict) -> tuple[float, dict]:
    w = cfg["priority_weights"]
    parts = {k: round(w.get(k, 0) * factors.get(k, 0), 2) for k in FACTORS}
    parts["unblocks"] = round(w["unblocks"] * unblocks, 2)
    return round(sum(parts.values()), 2), parts


def backlog_add(root: Path, title: str, category: str, severity: str, factors: dict, depends_on: list[str] | None = None, module: str = "") -> dict:
    if category not in CATEGORIES or severity not in SEVERITY:
        raise ValueError(f"category must be one of {CATEGORIES}; severity one of {list(SEVERITY)}")
    bl = load(root, "backlog.json", {})
    for d in depends_on or []:
        if d not in bl:
            raise ValueError(f"unknown dependency {d}")
    f = {k: max(0, min(3, int(factors.get(k, 0)))) for k in FACTORS}
    f["severity"] = f["severity"] or SEVERITY[severity]
    item = {"id": f"B{len(bl) + 1}", "title": title, "category": category, "severity": severity, "module": module, "factors": f, "depends_on": depends_on or [],
            "status": "not_started", "evidence": [], "blocker": None, "history": [{"ts": now_iso(), "status": "not_started"}]}
    bl[item["id"]] = item
    _rescore(root, bl)
    save(root, "backlog.json", bl)
    return bl[item["id"]]


def _rescore(root: Path, bl: dict) -> None:
    cfg = load_config("omega", root)
    for it in bl.values():
        unb = sum(1 for o in bl.values() if it["id"] in o["depends_on"] and o["status"] in OPEN + ("blocked",))
        it["priority"], it["priority_breakdown"] = priority(it["factors"], unb, cfg)
        it["p0"] = it["severity"] == "critical" and (it["factors"]["data_risk"] >= 3 or it["factors"]["security"] >= 3)


def backlog_update(root: Path, iid: str, status: str | None = None, evidence: list[str] | None = None, blocker: str | None = None, note: str | None = None) -> dict:
    bl = load(root, "backlog.json", {})
    if iid not in bl:
        raise ValueError(f"unknown item {iid}")
    it = bl[iid]
    if status:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        ev = (it["evidence"] if not evidence else it["evidence"] + evidence)
        good, bad = valid_evidence(root, ev)
        if status == "completed" and (not good or bad):
            raise ValueError("completed needs valid evidence" + (f"; rejected: {bad}" if bad else " (none given)"))
        if status == "blocked" and not (blocker or it["blocker"]):
            raise ValueError("blocked needs --blocker")
        if status == "out_of_scope" and not note:
            raise ValueError("out_of_scope needs a --note justification")
        if status == "failed" and it["status"] == "failed" and not evidence and not note:
            raise ValueError("a repeated failure needs new evidence or a changed hypothesis (--note)")
        it["status"] = status
        it["history"].append({"ts": now_iso(), "status": status, "note": note})
    if evidence:
        it["evidence"] = it["evidence"] + evidence
    if blocker:
        it["blocker"] = blocker
    _rescore(root, bl)
    save(root, "backlog.json", bl)
    return it


def ready(root: Path) -> list[dict]:
    bl = load(root, "backlog.json", {})
    out = [i for i in bl.values() if i["status"] in ("not_started", "in_progress") and all(bl[d]["status"] in ("completed", "out_of_scope") for d in i["depends_on"])]
    return sorted(out, key=lambda i: (not i["p0"], -i["priority"], i["id"]))


def accept_met(root: Path, aid: str, evidence: list[str]) -> dict:
    st = load(root, "state.json", None)
    a = next((x for x in st["acceptance"] if x["id"] == aid), None)
    if a is None:
        raise ValueError(f"unknown criterion {aid}")
    good, bad = valid_evidence(root, evidence)
    if not good or bad:
        raise ValueError("criterion needs valid evidence" + (f"; rejected: {bad}" if bad else ""))
    a["status"], a["evidence"] = "met", a["evidence"] + good
    save(root, "state.json", st)
    return a


def done(root: Path) -> dict:
    st = load(root, "state.json", None)
    if not st:
        return {"done": False, "state": "no_state", "reasons": ["run `omega.py init` first"]}
    bl = load(root, "backlog.json", {})
    reasons = []
    if not st["acceptance"]:
        reasons.append("no acceptance criteria defined")
    reasons += [f"criterion {a['id']} not met: {a['text'][:60]}" for a in st["acceptance"] if a["status"] != "met"]
    reasons += [f"{i['id']} {i['status']}: {i['title'][:60]}" for i in bl.values() if i["status"] in OPEN]
    blocked = [i["id"] for i in bl.values() if i["status"] == "blocked"]
    if reasons:
        return {"done": False, "state": "running", "reasons": reasons, "blocked": blocked}
    return {"done": True, "state": "done_with_blockers" if blocked else "done", "reasons": [], "blocked": blocked}


def score_set(root: Path, cat: str, value: float, evidence: list[str]) -> dict:
    cfg = load_config("omega", root)["scorecard"]
    if cat not in cfg:
        raise ValueError(f"category must be one of {list(cfg)}")
    if not 0 <= value <= cfg[cat]:
        raise ValueError(f"{cat} score must be 0..{cfg[cat]}")
    good, bad = valid_evidence(root, evidence)
    if not good or bad:
        raise ValueError("a score needs valid evidence" + (f"; rejected: {bad}" if bad else ""))
    sc = load(root, "scorecard.json", {})
    sc[cat] = {"score": value, "max": cfg[cat], "evidence": good, "ts": now_iso()}
    save(root, "scorecard.json", sc)
    return sc[cat]


def gate(root: Path) -> dict:
    cfg = load_config("omega", root)
    sc, bl, st = load(root, "scorecard.json", {}), load(root, "backlog.json", {}), load(root, "state.json", {})
    reasons = []
    total = sum(v["score"] for v in sc.values())
    missing = [c for c in cfg["scorecard"] if c not in sc]
    if missing:
        reasons.append(f"scorecard categories without evidence: {missing}")
    for it in list(bl.values()):
        good, bad = valid_evidence(root, it["evidence"]) if it["status"] == "completed" else ([], [])
        if bad:
            reasons.append(f"{it['id']} has unverifiable evidence: {bad[:1]}")
    for c, v in sc.items():
        g, b = valid_evidence(root, v["evidence"])
        if b:
            reasons.append(f"score {c} evidence no longer verifies: {b[:1]}")
    crit = [i for i in bl.values() if i["severity"] == "critical" and i["status"] not in ("completed", "out_of_scope")]
    if crit:
        reasons.append(f"open critical items: {[i['id'] for i in crit]}")
    high_sec = [i for i in bl.values() if i["category"] in ("security", "data_integrity") and i["severity"] == "high" and i["status"] in OPEN]
    if high_sec:
        reasons.append(f"open high security/data-integrity items: {[i['id'] for i in high_sec]} (resolve or have an authorized person risk-accept)")
    d = done(root)
    level = "not_ready"
    if not reasons:
        for lv in ("staging", "uat", "production"):
            if total >= cfg["readiness"][lv] and not missing:
                level = lv
        if level != "not_ready" and not d["done"]:
            reasons.append("acceptance criteria / backlog not complete: " + "; ".join(d["reasons"][:3]))
            level = "not_ready"
    appr = [a for a in load(root, "approvals.json", []) if a["status"] == "approved" and a.get("class") == "production_release"]
    if level == "production" and not appr:
        level, reasons = "uat", reasons + ["production needs a human-approved production_release entry (omega.py approvals)"]
    return {"level": level, "score": total, "max": 100, "reasons": reasons, "thresholds_are_assumptions": True,
            "note": "A passing gate is evidence-based, not external certification; production always needs a human decision."}


# ------------------------------------------------------------------ guard / approvals
def classify(root: Path | None, command: str) -> dict:
    cfg = load_config("omega", root)
    g = cfg.get("guard") or {}
    cmd = command.strip()
    for pat in g.get("deny") or []:
        if re.search(pat, cmd, re.I):
            return {"decision": "deny", "class": "denied", "reason": f"matches deny rule {pat[:40]}"}
    prot = (cfg.get("autonomy") or {}).get("protect_branches") or []
    m = re.search(r"\bgit\s+push\b(.*)", cmd)
    if m and prot and any(re.search(rf"(^|\s|:|/)({re.escape(b)})(\s|$)", m.group(1)) for b in prot):
        return {"decision": "queue", "class": "protected_branch_push", "reason": "push to a protected branch"}
    for pat in g.get("guarded") or []:
        if re.search(pat, cmd, re.I):
            return {"decision": "queue", "class": "guarded", "reason": f"matches guarded rule {pat[:40]}"}
    return {"decision": "allow", "class": "routine", "reason": "no rule matched"}


def approvals_add(root: Path, action: str, cls: str, reason: str) -> dict:
    ap = load(root, "approvals.json", [])
    ex = next((a for a in ap if a["action"] == action and a["status"] == "pending"), None)
    if ex:
        return ex
    rec = {"id": f"P{len(ap) + 1}", "action": action, "class": cls, "reason": reason, "status": "pending", "ts": now_iso(), "consumed": False}
    ap.append(rec)
    save(root, "approvals.json", ap)
    return rec


def approvals_resolve(root: Path, aid: str, approve: bool, by: str) -> dict:
    ap = load(root, "approvals.json", [])
    a = next((x for x in ap if x["id"] == aid), None)
    if a is None:
        raise ValueError(f"unknown approval {aid}")
    a["status"], a["by"], a["resolved"] = ("approved" if approve else "rejected"), by, now_iso()
    save(root, "approvals.json", ap)
    return a


def guard(root: Path, command: str) -> dict:
    r = classify(root, command)
    if r["decision"] == "queue":
        ap = load(root, "approvals.json", [])
        ok = next((a for a in ap if a["action"] == command and a["status"] == "approved" and not a["consumed"]), None)
        if ok:                                           # a human approved exactly this command: allow once
            ok["consumed"] = True
            save(root, "approvals.json", ap)
            return {"decision": "allow", "class": r["class"], "reason": f"approved by {ok.get('by')} ({ok['id']})"}
        rec = approvals_add(root, command, r["class"], r["reason"])
        r["approval_id"] = rec["id"]
        r["advice"] = "Do NOT run this. It is queued for a human; continue with independent backlog items."
    elif r["decision"] == "deny":
        r["advice"] = "Refused. Choose a safer approach."
    return r


def hook(root: Path, payload: dict) -> tuple[int, str]:
    """Claude Code PreToolUse: exit 2 blocks the tool call and feeds stderr back to the model."""
    if payload.get("tool_name") != "Bash":
        return 0, ""
    r = guard(root, (payload.get("tool_input") or {}).get("command", ""))
    if r["decision"] == "allow":
        return 0, ""
    return 2, f"AOS OMEGA guard: {r['decision']} ({r['reason']}). {r.get('advice', '')}" + (f" Approval id {r['approval_id']}." if r.get("approval_id") else "")


# ------------------------------------------------------------------ reporting
def report(root: Path) -> list[Path]:
    st, bl, sc = load(root, "state.json", {}), load(root, "backlog.json", {}), load(root, "scorecard.json", {})
    docs = root / "docs" / "implementation"
    docs.mkdir(parents=True, exist_ok=True)
    order = sorted(bl.values(), key=lambda i: (not i["p0"], -i["priority"], i["id"]))
    mb = ["# Master backlog", f"_Generated by omega.py {now_iso()}. Priority = transparent weighted score (config/omega.yaml); P0 = critical security/data-integrity._", "",
          "| ID | P0 | Pri | Sev | Category | Module | Title | Depends | Status |", "|---|---|---|---|---|---|---|---|---|"]
    mb += [f"| {i['id']} | {'P0' if i['p0'] else ''} | {i['priority']} | {i['severity']} | {i['category']} | {i['module']} | {i['title']} | {','.join(i['depends_on'])} | {i['status']} |" for i in order]
    counts = {s: sum(1 for i in bl.values() if i["status"] == s) for s in STATUSES}
    g, d = gate(root), done(root)
    cs = ["# Completion status", f"_Generated {now_iso()}. Statuses are backed by evidence; see MASTER_BACKLOG.md._", "", f"Goal: {st.get('goal', '')}", f"Overall: **{d['state']}** | gate: **{g['level']}** ({g['score']}/100)", "",
          "| Status | Items |", "|---|---|"] + [f"| {s} | {n} |" for s, n in counts.items()]
    cs += ["", "## Acceptance criteria"] + [f"- [{'x' if a['status'] == 'met' else ' '}] {a['id']} {a['text']}" + (f" - evidence: {a['evidence'][0]}" if a["evidence"] else "") for a in st.get("acceptance", [])]
    cs += ["", "## Scorecard (evidence-based)"] + [f"- {k}: {v['score']}/{v['max']} - {v['evidence'][0]}" for k, v in sc.items()] + [f"- missing: {[c for c in load_config('omega', root)['scorecard'] if c not in sc]}"]
    cs += ["", "## Gate reasons"] + [f"- {r}" for r in g["reasons"]] + ["", "## Pending approvals"] + [f"- {a['id']} [{a['class']}] {a['action'][:100]} - {a['reason']}" for a in load(root, "approvals.json", []) if a["status"] == "pending"]
    p1, p2 = docs / "MASTER_BACKLOG.md", docs / "COMPLETION_STATUS.md"
    p1.write_text("\n".join(mb) + "\n", encoding="utf-8")
    p2.write_text("\n".join(cs) + "\n", encoding="utf-8")
    return [p1, p2]


def checkpoint(root: Path) -> Path:
    st, bl = load(root, "state.json", {}), load(root, "backlog.json", {})
    g = git_state(root)
    nxt = ready(root)[:5]
    t = [f"# OMEGA checkpoint {now_iso()}", f"Goal: {st.get('goal')}", f"Git: {g['branch']} @ {g['commit'][:12]} dirty={len(g['dirty_files'])}", "", "## Next (dependency-ready)"] + [f"- {i['id']} {i['title']} (pri {i['priority']})" for i in nxt]
    t += ["", "## In progress / failed / blocked"] + [f"- {i['id']} {i['status']}: {i['title']}" + (f" - {i['blocker']}" if i["blocker"] else "") for i in bl.values() if i["status"] in ("in_progress", "failed", "blocked")]
    t += ["", f"Done check: {json.dumps(done(root))[:300]}", "", "Resume: read this file, `omega.py status`, verify git state, continue with `omega.py next`. Do not redo completed items."]
    p = odir(root) / "CHECKPOINT.md"
    p.write_text(redact("\n".join(t)) + "\n", encoding="utf-8")
    return p


def status(root: Path) -> dict:
    st = load(root, "state.json", None)
    if not st:
        return {"initialized": False}
    bl = load(root, "backlog.json", {})
    return {"initialized": True, "goal": st["goal"], "phase": st["phase"], "done": done(root), "items": {s: sum(1 for i in bl.values() if i["status"] == s) for s in STATUSES},
            "next": [{"id": i["id"], "title": i["title"], "priority": i["priority"], "p0": i["p0"]} for i in ready(root)[:3]],
            "pending_approvals": sum(1 for a in load(root, "approvals.json", []) if a["status"] == "pending")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init"); i.add_argument("--goal", required=True); i.add_argument("--repo", default=""); i.add_argument("--project", default=""); i.add_argument("--accept", action="append", default=[])
    a = sub.add_parser("accept"); a.add_argument("action", choices=["add", "met"]); a.add_argument("arg"); a.add_argument("--evidence", action="append", default=[])
    b = sub.add_parser("backlog"); b.add_argument("action", choices=["add", "update", "list"]); b.add_argument("id", nargs="?")
    b.add_argument("--title"); b.add_argument("--category"); b.add_argument("--severity", default="medium"); b.add_argument("--factors", default="{}"); b.add_argument("--depends-on", default=""); b.add_argument("--module", default="")
    b.add_argument("--status"); b.add_argument("--evidence", action="append", default=[]); b.add_argument("--blocker"); b.add_argument("--note")
    sub.add_parser("next")
    f = sub.add_parser("feature"); f.add_argument("action", choices=["add", "list"]); f.add_argument("--module"); f.add_argument("--name"); f.add_argument("--status", choices=FEATURE_STATUSES); f.add_argument("--evidence", action="append", default=[]); f.add_argument("--missing", default="")
    s = sub.add_parser("score"); s.add_argument("action", choices=["set", "show"]); s.add_argument("category", nargs="?"); s.add_argument("value", nargs="?", type=float); s.add_argument("--evidence", action="append", default=[])
    g = sub.add_parser("gate"); g.add_argument("--json", action="store_true")
    d = sub.add_parser("done"); d.add_argument("--json", action="store_true")
    ap_ = sub.add_parser("approvals"); ap_.add_argument("action", choices=["list", "resolve"]); ap_.add_argument("id", nargs="?"); ap_.add_argument("--approve", action="store_true"); ap_.add_argument("--reject", action="store_true"); ap_.add_argument("--by", default="")
    gd = sub.add_parser("guard"); gd.add_argument("--command", required=True)
    sub.add_parser("hook"); sub.add_parser("checkpoint"); sub.add_parser("report")
    st_ = sub.add_parser("status"); st_.add_argument("--json", action="store_true")
    x = ap.parse_args(argv)
    root = find_root(x.root)
    pj = lambda o: print(json.dumps(o, indent=2, default=str))
    try:
        if x.cmd == "init":
            pj(init(root, x.goal, x.repo, x.project, x.accept))
        elif x.cmd == "accept":
            if x.action == "add":
                st = load(root, "state.json", None)
                st["acceptance"].append({"id": f"A{len(st['acceptance']) + 1}", "text": x.arg, "status": "open", "evidence": []}); save(root, "state.json", st); pj(st["acceptance"][-1])
            else:
                pj(accept_met(root, x.arg, x.evidence))
        elif x.cmd == "backlog":
            if x.action == "add":
                pj(backlog_add(root, x.title, x.category, x.severity, json.loads(x.factors), [d_ for d_ in x.depends_on.split(",") if d_], x.module))
            elif x.action == "update":
                pj(backlog_update(root, x.id, x.status, x.evidence, x.blocker, x.note))
            else:
                bl = load(root, "backlog.json", {})
                for it in sorted(bl.values(), key=lambda i: (not i["p0"], -i["priority"])):
                    print(f"{it['id']:5} {'P0' if it['p0'] else '  '} {it['priority']:6} {it['severity']:8} {it['status']:18} {it['title']}")
        elif x.cmd == "next":
            n = ready(root)
            pj(n[0] if n else {"next": None, "note": "nothing dependency-ready: check blocked/failed items or `omega.py done`"})
        elif x.cmd == "feature":
            st = load(root, "state.json", None)
            if x.action == "add":
                good, bad = valid_evidence(root, x.evidence)
                if x.status == "COMPLETE_VERIFIED" and (not good or bad):
                    raise ValueError("COMPLETE_VERIFIED needs valid evidence")
                st["features"].append({"module": x.module, "name": x.name, "status": x.status, "missing": x.missing, "evidence": good}); save(root, "state.json", st); pj(st["features"][-1])
            else:
                print("| Module | Feature | Status | Missing | Evidence |\n|---|---|---|---|---|")
                for ft in st["features"]:
                    print(f"| {ft['module']} | {ft['name']} | {ft['status']} | {ft['missing']} | {', '.join(ft['evidence'][:1])} |")
        elif x.cmd == "score":
            pj(score_set(root, x.category, x.value, x.evidence) if x.action == "set" else load(root, "scorecard.json", {}))
        elif x.cmd == "gate":
            r = gate(root); pj(r) if x.json else print(f"gate: {r['level']} ({r['score']}/100)\n" + "\n".join(" - " + m for m in r["reasons"]))
            return 0 if r["level"] != "not_ready" else 1
        elif x.cmd == "done":
            r = done(root); pj(r) if x.json else print(r["state"] + ("\n" + "\n".join(" - " + m for m in r["reasons"][:8]) if r["reasons"] else ""))
            return 0 if r["done"] else 1
        elif x.cmd == "approvals":
            if x.action == "list":
                pj(load(root, "approvals.json", []))
            else:
                pj(approvals_resolve(root, x.id, x.approve and not x.reject, x.by or "human"))
        elif x.cmd == "guard":
            r = guard(root, x.command); pj(r)
            return {"allow": 0, "queue": 3, "deny": 4}[r["decision"]]
        elif x.cmd == "hook":
            try:
                payload = json.loads(sys.stdin.read() or "{}")
            except json.JSONDecodeError:
                return 0
            code, msg = hook(Path(payload.get("cwd") or root) if payload.get("cwd") else root, payload)
            if msg:
                print(msg, file=sys.stderr)
            return code
        elif x.cmd == "checkpoint":
            print(checkpoint(root))
        elif x.cmd == "report":
            print("\n".join(str(p) for p in report(root)))
        else:
            pj(status(root))
    except (ValueError, TypeError) as e:
        print(f"omega error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
