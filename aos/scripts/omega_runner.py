#!/usr/bin/env python3
"""OMEGA supervisor: keeps an agent working until the goal is met, WAITING (not bypassing) when a usage limit is hit.

  omega_runner.py run --goal "..." [--repo URL] [--tool claude|codex|custom] [--cmd "tool -p {prompt}"] [--permission-mode auto] [--accept "criterion" ...]
  omega_runner.py status | stop | setup-hook [--global | --target PROJECT]

Loop: goal met? -> done. Else run one agent cycle (headless) -> if the output shows a usage/rate limit: parse the reset time (or back off),
sleep until then (heartbeats; `stop` interrupts), resume with the short continuation prompt -> otherwise measure progress; stop when
stalled (no change in git/ledger for N cycles), failing, out of cycles, or stopped. Waits longer than max_wait_hours exit with code 75 and a
resume time so cron/a scheduler can restart it. Limits are never evaded: no account switching, no retries faster than the provider allows.
Exit codes: 0 done (or done with blockers), 20 stalled, 21 max cycles, 22 stopped, 23 repeated failures, 24 already running, 75 long wait (resume later).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from aos_lib import AOS_DIR, find_root, load_config, now_iso, redact, run, state_dir
import omega

LIMIT_RX = re.compile(r"(usage|rate|5[- ]?hour|weekly|session|message|token)[ -]limit[^.\n]{0,40}(reached|exceeded|hit)|hit your (usage )?limit|\blimit (reached|exceeded)\b|limit (will )?reset|"
                      r"rate_limit_error|too many requests|\b429\b|quota (exceeded|exhausted)|out of (usage|credits)", re.I)
TRANSIENT_RX = re.compile(r"overloaded|temporarily unavailable|\b(502|503|529)\b|connection (reset|timed out)|ECONNRESET|ETIMEDOUT", re.I)
UNIT = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "d": 86400, "day": 86400}


def parse_wait(text: str, now: datetime) -> float | None:
    """Seconds until the provider says capacity returns, or None if the text gives no usable time."""
    m = re.search(r"retry[- ]after[:= ]+(\d+)", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"reset[^\d\n]{0,25}(\d{10})(?!\d)", text, re.I)
    if m:
        return max(0.0, float(m.group(1)) - now.timestamp())
    m = re.search(r"(?:try again|retry|resets?|available again|come back)\s*(?:in|after)\s*((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,and]*)+)", text, re.I)
    if m:
        tot = sum(int(n) * UNIT[re.sub(r"s$", "", u.lower()) if len(u) > 1 else u.lower()] for n, u in re.findall(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", m.group(1), re.I))
        return float(tot) if tot else None
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([A-Za-z_]+/[A-Za-z_]+)\))?", text, re.I)
    if m:
        h, mi, ap, tzn = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            return None
        base = now
        if tzn:
            try:
                from zoneinfo import ZoneInfo
                base = now.astimezone(ZoneInfo(tzn))
            except Exception:                          # unknown tz: fall back to local interpretation
                base = now
        tgt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if tgt <= base:
            tgt += timedelta(days=1)
        return (tgt - base).total_seconds()
    return None


def detect(text: str, now: datetime | None = None) -> dict | None:
    now = (now or datetime.now().astimezone())
    if LIMIT_RX.search(text):
        return {"kind": "limit", "wait_s": parse_wait(text, now)}
    if TRANSIENT_RX.search(text):
        return {"kind": "transient", "wait_s": None}
    return None


def build_cmd(tool: str, prompt: str, resume: bool, mode: str, custom: str | None, extra: list[str]) -> list[str]:
    if custom:
        return [prompt if t == "{prompt}" else t.replace("{prompt}", prompt) for t in shlex.split(custom)]
    if tool == "claude":
        return ["claude", "-p", prompt, "--agent", "omega", "--permission-mode", mode] + (["--continue"] if resume else []) + extra
    if tool == "codex":                                   # preset not verified in the build sandbox (codex not installed)
        return (["codex", "exec", "resume", "--last", prompt] if resume else ["codex", "exec", prompt]) + extra
    raise SystemExit("--tool must be claude or codex, or pass --cmd with {prompt}")


def signature(root: Path) -> str:
    h = hashlib.sha256()
    h.update(run(["git", "rev-parse", "HEAD"], cwd=root)[1].encode())
    h.update(run(["git", "status", "--porcelain", "-uall"], cwd=root)[1].encode())
    h.update(run(["git", "diff", "HEAD"], cwd=root, timeout=120)[1].encode())
    for n in ("state.json", "backlog.json", "scorecard.json", "approvals.json"):
        p = omega.odir(root) / n
        h.update(p.read_bytes() if p.exists() else b"-")
    return h.hexdigest()


def initial_prompt(goal: str, accept: list[str]) -> str:
    return (f"Run Project OMEGA on this repository. Goal: {goal}\n"
            + (("Acceptance criteria: " + "; ".join(accept) + "\n") if accept else "")
            + "Use the aos-omega skill. If `omega.py status` shows no state, initialize it. Work through the backlog in dependency order until `omega.py done` succeeds. "
              "Do not wait for approval: guarded actions are queued by the guard (continue with independent items). Record evidence for every completed item. "
              "Run `omega.py checkpoint` and `omega.py report` at each milestone.")


def continue_prompt() -> str:
    for p in (AOS_DIR / "skills" / "aos-omega" / "references" / "CONTINUE_PROMPT.md", AOS_DIR / "modules" / "references" / "aos-omega" / "references" / "CONTINUE_PROMPT.md"):
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "Continue Project OMEGA from the current repository state and the existing ledger (`omega.py status`). Do not restart. Complete the highest-priority dependency-ready items with evidence."


def default_exec(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        return 124, f"cycle timeout after {timeout}s\n{(e.stdout or '')[-500:] if isinstance(e.stdout, str) else ''}"
    except OSError as e:
        return 127, f"cannot start {cmd[0]}: {e}"


def rstate(root: Path, **kw) -> dict:
    p = omega.odir(root) / "runner.json"
    d = json.loads(p.read_text()) if p.exists() else {}
    d.update(kw, updated=now_iso())
    p.write_text(json.dumps(d, indent=2))
    return d


def _lock(root: Path) -> bool:
    """One supervisor per project: a live PID in runner.lock means another instance owns the loop (safe for cron restarts)."""
    p = omega.odir(root) / "runner.lock"
    if p.exists():
        try:
            pid = int(p.read_text().strip())
            os.kill(pid, 0)
            if pid != os.getpid():
                return False
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    p.write_text(str(os.getpid()))
    return True


def supervise(root: Path, goal: str, accept: list[str] | None = None, tool: str = "claude", custom: str | None = None, mode: str = "auto", extra: list[str] | None = None,
              execute=default_exec, sleep=time.sleep, now=lambda: datetime.now().astimezone(), cycle_timeout: int = 7200, max_cycles: int | None = None) -> int:
    cfg = load_config("omega", root)["runner"]
    max_cycles = max_cycles or cfg["max_cycles"]
    if not _lock(root):
        print("another omega_runner is already running for this project", file=sys.stderr)
        return 24
    try:
        return _supervise(root, goal, accept, tool, custom, mode, extra, execute, sleep, now, cycle_timeout, max_cycles, cfg)
    finally:
        (omega.odir(root) / "runner.lock").unlink(missing_ok=True)


def _supervise(root, goal, accept, tool, custom, mode, extra, execute, sleep, now, cycle_timeout, max_cycles, cfg) -> int:
    if not (omega.odir(root) / "state.json").exists():
        omega.init(root, goal, accept=accept or [])
    stop = omega.odir(root) / "STOP"
    runs = omega.odir(root) / "runs.jsonl"
    cycles = stalls = fails = limit_hits = 0
    last = signature(root)
    started_before = (omega.odir(root) / "runs.jsonl").exists()
    while True:
        if stop.exists():
            rstate(root, status="stopped"); return 22
        d = omega.done(root)
        if d["done"]:
            omega.report(root); rstate(root, status=d["state"]); return 0
        if cycles >= max_cycles:
            rstate(root, status="max_cycles"); return 21
        resume = cycles > 0 or started_before
        prompt = continue_prompt() if resume else initial_prompt(goal, accept or [])
        t0 = time.time()
        rc, out = execute(build_cmd(tool, prompt, resume, mode, custom, extra or []), root, cycle_timeout)
        out = redact(out)
        lim = detect(out, now())
        rec = {"ts": now_iso(), "cycle": cycles + 1, "rc": rc, "seconds": round(time.time() - t0, 1), "limit": lim, "tail": out[-600:]}
        with runs.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        started_before = True
        if lim:
            limit_hits += 1 if lim["kind"] == "limit" else 0
            if lim["kind"] == "limit":
                bo = cfg["backoff_s"]
                wait = lim["wait_s"] if lim["wait_s"] is not None else bo[min(limit_hits - 1, len(bo) - 1)]
                wait = max(float(cfg["min_wait_s"]), wait + float(cfg.get("margin_s", 60)))          # margin; never retry sooner than the provider allows
            else:
                wait = float(cfg["min_wait_s"]); fails += 1
                if fails >= 6:
                    rstate(root, status="failing"); return 23
            until = now() + timedelta(seconds=wait)
            if wait > cfg["max_wait_hours"] * 3600:
                rstate(root, status="waiting_long", resume_at=until.isoformat(), reason=lim["kind"])
                print(f"limit resets in ~{wait / 3600:.1f}h (> max_wait_hours): restart this command at {until.isoformat()} (cron/scheduler).", file=sys.stderr)
                return 75
            rstate(root, status="waiting", resume_at=until.isoformat(), reason=lim["kind"], cycle=cycles)
            omega.checkpoint(root)                                           # state is safe on disk while we wait
            while now() < until:                                              # injected clock: real in production, fake in tests
                if stop.exists():
                    rstate(root, status="stopped"); return 22
                sleep(min(60.0, max(1.0, (until - now()).total_seconds())))
            continue
        limit_hits = 0
        cycles += 1
        if rc != 0:
            fails += 1
            if fails >= 3:
                rstate(root, status="failing", last_rc=rc); return 23
            sleep(min(300.0, 30.0 * fails))
            continue
        fails = 0
        sig = signature(root)
        stalls = stalls + 1 if sig == last else 0
        last = sig
        rstate(root, status="running", cycle=cycles, stalls=stalls)
        if stalls >= cfg["stall_cycles"]:
            omega.checkpoint(root); rstate(root, status="stalled"); return 20


def setup_hook(settings: Path, script: str) -> dict:
    """Merge a PreToolUse(Bash) guard hook into a Claude Code settings.json without touching other settings."""
    cur = json.loads(settings.read_text()) if settings.exists() else {}
    cmd = f"python3 {script} hook"
    pre = cur.setdefault("hooks", {}).setdefault("PreToolUse", [])
    if not any(h.get("command") == cmd for e in pre for h in e.get("hooks", [])):
        pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(cur, indent=2))
    return cur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--goal", required=True); r.add_argument("--repo"); r.add_argument("--accept", action="append", default=[])
    r.add_argument("--tool", default="claude"); r.add_argument("--cmd", dest="custom_cmd"); r.add_argument("--permission-mode", default="auto")
    r.add_argument("--max-cycles", type=int); r.add_argument("--cycle-timeout", type=int, default=7200); r.add_argument("--extra", default="")
    sub.add_parser("status"); sub.add_parser("stop")
    s = sub.add_parser("setup-hook"); s.add_argument("--global", dest="glob", action="store_true"); s.add_argument("--target")
    a = ap.parse_args(argv)
    root = find_root(a.root)
    if a.cmd == "run":
        if a.repo:
            name = a.repo.rstrip("/").removesuffix(".git").split("/")[-1]
            if not (root / ".git").exists():
                root = root / name
                if not root.exists():
                    rc, out = run(["git", "clone", a.repo, str(root)], timeout=900)
                    if rc != 0:
                        print(f"clone failed: {out[-300:]}", file=sys.stderr); return 2
        return supervise(root, a.goal, a.accept, a.tool, a.custom_cmd, a.permission_mode, shlex.split(a.extra), cycle_timeout=a.cycle_timeout, max_cycles=a.max_cycles)
    if a.cmd == "status":
        p = omega.odir(root) / "runner.json"
        print(p.read_text() if p.exists() else "{}"); print(json.dumps(omega.status(root), indent=2)); return 0
    if a.cmd == "stop":
        (omega.odir(root) / "STOP").write_text(now_iso()); print("stop requested (takes effect between cycles / during waits)"); return 0
    script = str(AOS_DIR / "scripts" / "omega.py")
    target = Path.home() / ".claude" / "settings.json" if a.glob else Path(a.target or root) / ".claude" / "settings.json"
    setup_hook(target, script)
    print(f"guard hook installed in {target} (verify with /hooks in Claude Code)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
