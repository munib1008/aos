#!/usr/bin/env python3
"""Dual-agent coordination for ONE repository worked on by two coding agents (e.g. Codex = platform owner/integrator, Claude Code = engines).

  dualagent.py ownership --file MODULE_OWNERSHIP.md [--path src/x.ts]     # parsed globs per agent / owner of a path
  dualagent.py check --agent claude --base origin/codex/platform-foundation [--head HEAD]   # did this branch touch only its own files? exit 1 if not
  dualagent.py hook                                                        # Claude Code PreToolUse hook: blocks Edit/Write on the other agent's files
  dualagent.py init --base REF --slice NAME [--agent claude]               # isolated git worktree + branch for one agent slice, writes .aos/dual/role
  dualagent.py status [--base REF]                                         # worktrees, branches, ahead/behind, roles
  dualagent.py review-packet --agent claude --base REF                     # review request for the OTHER agent (ownership check, diffstat, commands to run)

Ownership comes from the project's own file (default MODULE_OWNERSHIP.md): backticked path globs in the section that mentions "Codex" or "Claude".
Root-level files are the integrator's (codex). Unowned paths are reported, not silently allowed. Role = env AOS_AGENT_ROLE or .aos/dual/role.
Limits: the hook only sees Edit/Write-type tools (not shell writes); `check` is the authoritative gate and belongs in CI/PR review. Never merges anything.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

from aos_lib import atomic_write, find_root, now_iso, run, state_dir

AGENTS = ("codex", "claude")
GLOB_OK = re.compile(r"^[\w.\-/*@]+$")


def parse_ownership(text: str) -> dict[str, list[str]]:
    own: dict[str, list[str]] = {a: [] for a in AGENTS}
    cur = None
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s", line):
            h = line.lower()
            cur = "codex" if "codex" in h else "claude" if "claude" in h else None
            continue
        if cur and line.lstrip().startswith(("-", "*")):
            for tok in re.findall(r"`([^`]+)`", line):
                if GLOB_OK.match(tok) and ("/" in tok or "*" in tok) and not tok.startswith("@"):
                    own[cur].append(tok)
    return own


def _rx(glob: str) -> re.Pattern:
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"; i += 3
        elif glob.startswith("**", i):
            out += ".*"; i += 2
        elif glob[i] == "*":
            out += "[^/]*"; i += 1
        else:
            out += re.escape(glob[i]); i += 1
    return re.compile("^" + out + "$")


def owner_of(path: str, own: dict[str, list[str]]) -> str | None:
    path = path.lstrip("./")
    for a in AGENTS:
        if any(_rx(g).match(path) for g in own[a]):
            return a
    if "/" not in path:
        return "codex"                      # root governance/config files belong to the integrator
    return None


def changed_files(root: Path, base: str, head: str = "HEAD") -> list[str]:
    rc, out = run(["git", "diff", "--name-only", f"{base}...{head}"], cwd=root, timeout=120)
    if rc != 0:
        rc, out = run(["git", "diff", "--name-only", base, head], cwd=root, timeout=120)
    return [l for l in out.splitlines() if l.strip() and not l.startswith(".aos/")]       # tool state is never part of the change


def check(root: Path, agent: str, base: str, head: str = "HEAD", ownership: Path | None = None, allow_unowned: bool = False) -> dict:
    f = ownership or root / "MODULE_OWNERSHIP.md"
    if not f.exists():                         # ownership file may live on the base branch only
        rc, txt = run(["git", "show", f"{base}:{f.name}"], cwd=root)
        text = txt if rc == 0 else ""
    else:
        text = f.read_text(encoding="utf-8")
    own = parse_ownership(text)
    if not any(own.values()):
        return {"ok": False, "error": "no ownership globs parsed (check the ownership file format)", "violations": [], "unowned": [], "files": []}
    files = changed_files(root, base, head)
    other = "codex" if agent == "claude" else "claude"
    viol = [x for x in files if owner_of(x, own) == other]
    unowned = [x for x in files if owner_of(x, own) is None]
    return {"agent": agent, "base": base, "files": files, "violations": viol, "unowned": unowned,
            "ok": not viol and (allow_unowned or not unowned),
            "advice": ("Move contract/core changes into a separate proposal for the owner; engine code must not edit owner-controlled paths." if viol else
                       ("Unassigned paths: add them to the ownership file in a reviewed change." if unowned and not allow_unowned else "within ownership"))}


def role(root: Path) -> str | None:
    r = os.environ.get("AOS_AGENT_ROLE", "").strip().lower()
    if r in AGENTS:
        return r
    p = root / ".aos" / "dual" / "role"
    return p.read_text().strip().lower() if p.exists() and p.read_text().strip().lower() in AGENTS else None


def hook(root: Path, payload: dict) -> tuple[int, str]:
    if payload.get("tool_name") not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0, ""
    me = role(root)
    fp = (payload.get("tool_input") or {}).get("file_path") or (payload.get("tool_input") or {}).get("notebook_path") or ""
    if not me or not fp:
        return 0, ""
    try:
        rel = str(Path(fp).resolve().relative_to(root.resolve()))
    except ValueError:
        return 0, ""
    own_file = root / "MODULE_OWNERSHIP.md"
    if not own_file.exists():
        return 0, ""
    o = owner_of(rel, parse_ownership(own_file.read_text(encoding="utf-8")))
    if o and o != me:
        return 2, f"AOS dual-agent guard: {rel} is owned by {o}. Do not edit it; write a change proposal (contract/core change) for {o} to review instead."
    return 0, ""


def init_worktree(root: Path, base: str, slice_: str, agent: str = "claude") -> dict:
    branch = f"{agent}/{slice_}"
    wt = root.parent / f"{root.name}-{agent}-{slice_}"
    if wt.exists():
        return {"error": f"{wt} already exists"}
    rc, out = run(["git", "worktree", "add", "-b", branch, str(wt), base], cwd=root, timeout=120)
    if rc != 0:
        return {"error": out.strip()[-300:]}
    (wt / ".aos" / "dual").mkdir(parents=True, exist_ok=True)
    (wt / ".aos" / "dual" / "role").write_text(agent)
    return {"worktree": str(wt), "branch": branch, "base": base, "role": agent}


def status(root: Path, base: str | None = None) -> list[dict]:
    rc, out = run(["git", "worktree", "list", "--porcelain"], cwd=root)
    rows, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                rows.append(cur); cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v
    res = []
    for r in rows:
        p = Path(r["worktree"])
        d = {"path": str(p), "branch": r.get("branch", "").replace("refs/heads/", ""), "head": r.get("HEAD", "")[:10], "role": role(p)}
        if base and d["branch"]:
            rc, o = run(["git", "rev-list", "--left-right", "--count", f"{base}...{d['branch']}"], cwd=root)
            if rc == 0:
                behind, ahead = o.split()
                d.update(behind=int(behind), ahead=int(ahead))
        res.append(d)
    return res


def review_packet(root: Path, agent: str, base: str) -> Path:
    c = check(root, agent, base)
    other = "codex" if agent == "claude" else "claude"
    rc, br = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    rc, stat = run(["git", "diff", "--stat", f"{base}...HEAD"], cwd=root)
    rc, log = run(["git", "log", "--oneline", f"{base}..HEAD"], cwd=root)
    t = [f"# Review request: {br.strip()} ({agent} -> {other})", f"Created {now_iso()}; base `{base}`.", "",
         f"Ownership check: **{'PASS' if c['ok'] else 'FAIL'}** - {c.get('advice', c.get('error'))}"]
    if c["violations"]:
        t += ["Files owned by the other agent:"] + [f"- {x}" for x in c["violations"]]
    if c["unowned"]:
        t += ["Unassigned paths:"] + [f"- {x}" for x in c["unowned"]]
    t += ["", "## Commits", log.strip() or "(none)", "", "## Diffstat", "```", stat.strip(), "```", "",
          "## Reviewer checklist (the reviewing agent must verify, not assume)",
          "- contract compatibility and versions; migrations; authorization and tenant isolation; exact money/units; unsupported claims in AI output; prompt-injection handling of untrusted text; test evidence (run the commands yourself); unnecessary complexity.", "",
          "## How to run the review",
          f"- In Claude Code with the Codex plugin: `/codex:adversarial-review --base {base} <focus: auth, tenant isolation, money, race conditions>` (read-only), then `/codex:status`, `/codex:result`.",
          f"- Codex CLI directly: `codex review \"Review branch {br.strip()} against {base}: <focus>\"` from this worktree.",
          "- Findings go to the author as a list of verified defects with file:line and a failing test or reproduction where possible; the author fixes, the integrator merges."]
    p = state_dir(root) / "dual"
    p.mkdir(exist_ok=True)
    out = p / f"REVIEW_{re.sub(r'[^A-Za-z0-9_.-]+', '_', br.strip())}.md"
    atomic_write(out, "\n".join(t) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("ownership"); o.add_argument("--file", default="MODULE_OWNERSHIP.md"); o.add_argument("--path")
    c = sub.add_parser("check"); c.add_argument("--agent", choices=AGENTS, required=True); c.add_argument("--base", required=True); c.add_argument("--head", default="HEAD")
    c.add_argument("--ownership"); c.add_argument("--allow-unowned", action="store_true")
    sub.add_parser("hook")
    i = sub.add_parser("init"); i.add_argument("--base", required=True); i.add_argument("--slice", dest="slice_", required=True); i.add_argument("--agent", choices=AGENTS, default="claude")
    s = sub.add_parser("status"); s.add_argument("--base")
    r = sub.add_parser("review-packet"); r.add_argument("--agent", choices=AGENTS, required=True); r.add_argument("--base", required=True)
    a = ap.parse_args(argv)
    root = find_root(a.root)
    pj = lambda x: print(json.dumps(x, indent=2))
    if a.cmd == "ownership":
        f = Path(a.file) if Path(a.file).is_absolute() else root / a.file
        own = parse_ownership(f.read_text(encoding="utf-8"))
        pj({**own, "owner_of_path": owner_of(a.path, own) if a.path else None})
    elif a.cmd == "check":
        res = check(root, a.agent, a.base, a.head, Path(a.ownership) if a.ownership else None, a.allow_unowned); pj(res)
        return 0 if res["ok"] else 1
    elif a.cmd == "hook":
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            return 0
        code, msg = hook(Path(payload.get("cwd") or root), payload)
        if msg:
            print(msg, file=sys.stderr)
        return code
    elif a.cmd == "init":
        res = init_worktree(root, a.base, a.slice_, a.agent); pj(res)
        return 1 if "error" in res else 0
    elif a.cmd == "status":
        pj(status(root, a.base))
    else:
        print(review_packet(root, a.agent, a.base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
