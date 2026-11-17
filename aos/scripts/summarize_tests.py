#!/usr/bin/env python3
"""Run a test command (or parse a saved log) and return a compact, honest summary.

  summarize_tests.py run -- python3 -m unittest discover -s aos/tests
  summarize_tests.py parse build/test.log --exit-code 1

The full (redacted) log is saved under .aos/artifacts/. Status is PASSED only when exit code is 0
AND no failures were parsed. A non-zero exit with nothing parsed is reported FAILED (unparsed).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from aos_lib import find_root, load_config, redact, state_dir


def parse(log: str) -> dict:
    res = {"passed": None, "failed": None, "skipped": None, "total": None, "failures": [], "excerpts": []}
    m = re.search(r"Ran (\d+) tests? in", log)                                   # unittest
    if m:
        res["total"] = int(m.group(1))
        f = re.search(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?", log)
        res["failed"] = int(f.group(1) or 0) + int(f.group(2) or 0) if f else 0
        res["passed"] = res["total"] - res["failed"]
        res["failures"] += re.findall(r"^(?:FAIL|ERROR): (.+)$", log, re.M)
    m = re.search(r"=+ (?:(\d+) failed)?[, ]*(?:(\d+) passed)?", log)             # pytest
    if m and (m.group(1) or m.group(2)) and "Ran " not in log:
        res["failed"], res["passed"] = int(m.group(1) or 0), int(m.group(2) or 0)
        res["total"] = res["failed"] + res["passed"]
        res["failures"] += re.findall(r"^FAILED (\S+)", log, re.M)
    m = re.search(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) skipped, )?(?:(\d+) passed, )?(\d+) total", log)  # jest
    if m:
        res["failed"], res["skipped"], res["passed"], res["total"] = (int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0), int(m.group(4)))
        res["failures"] += re.findall(r"^\s*● (.+)$", log, re.M)
    if re.search(r"^# (pass|fail) \d+", log, re.M):                              # node:test
        p, f = re.search(r"^# pass (\d+)", log, re.M), re.search(r"^# fail (\d+)", log, re.M)
        res["passed"], res["failed"] = int(p.group(1)) if p else 0, int(f.group(1)) if f else 0
        res["total"] = res["passed"] + res["failed"]
        res["failures"] += re.findall(r"^not ok \d+ - (.+)$", log, re.M)
    m = re.search(r"(\d+) tests completed, (\d+) failed", log)                   # gradle
    if m:
        res["total"], res["failed"] = int(m.group(1)), int(m.group(2))
        res["passed"] = res["total"] - res["failed"]
    res["failures"] += re.findall(r"^(\S+ > \S+.*) FAILED$", log, re.M)
    res["failures"] += re.findall(r"^\s*> Task (\S+) FAILED", log, re.M)
    res["failures"] = sorted(set(res["failures"]))[:30]
    lines = log.splitlines()
    for i, l in enumerate(lines):
        if re.search(r"(Traceback|AssertionError|Error:|FAILED|Exception|error:)", l) and len(res["excerpts"]) < 5:
            res["excerpts"].append("\n".join(lines[max(0, i - 1):i + 5]))
    return res


def summarize(cmd: str, code: int, log: str, log_path: Path | None, max_lines: int) -> dict:
    p = parse(log)
    if code == 0 and not p["failures"] and not p["failed"]:
        status = "PASSED"
    elif p["failures"] or p["failed"]:
        status = "FAILED"
    else:
        status = "FAILED (unparsed)" if code else "PASSED"
    return {"command": cmd, "exit_code": code, "status": status, **{k: p[k] for k in ("total", "passed", "failed", "skipped")},
            "failure_ids": p["failures"], "error_excerpts": [e[:600] for e in p["excerpts"]][:max_lines // 10 or 3],
            "log_lines": log.count("\n") + 1, "full_log": str(log_path) if log_path else None}


def render(s: dict) -> str:
    o = [f"**{s['status']}** `{s['command']}` exit={s['exit_code']} total={s['total']} passed={s['passed']} failed={s['failed']}"]
    if s["failure_ids"]:
        o.append("Failing: " + "; ".join(s["failure_ids"][:15]))
    for e in s["error_excerpts"][:3]:
        o.append("```\n" + e + "\n```")
    if s["full_log"]:
        o.append(f"Full log ({s['log_lines']} lines): {s['full_log']}")
    return "\n".join(o)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root"); ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--timeout", type=int, default=1800); r.add_argument("argv", nargs=argparse.REMAINDER)
    p = sub.add_parser("parse"); p.add_argument("file"); p.add_argument("--exit-code", type=int, default=1)
    a = ap.parse_args(argv)
    root = find_root(a.root)
    cfg = load_config("optimization", root)["tool_output"]
    art = root / cfg["artifact_dir"]
    art.mkdir(parents=True, exist_ok=True)
    state_dir(root)
    if a.cmd == "run":
        cmd = [c for c in a.argv if c != "--"]
        if not cmd:
            ap.error("no command given")
        t0 = time.time()
        try:
            pr = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=a.timeout)
            code, log = pr.returncode, pr.stdout + "\n" + pr.stderr
        except subprocess.TimeoutExpired as e:
            code, log = 124, f"TIMEOUT after {a.timeout}s\n{(e.stdout or b'').decode() if isinstance(e.stdout, bytes) else (e.stdout or '')}"
        except OSError as e:
            code, log = 127, str(e)
        log = redact(log)
        lp = art / f"test-{int(t0)}.log"
        lp.write_text(log, encoding="utf-8")
        s = summarize(" ".join(cmd), code, log, lp, cfg["max_inline_lines"])
        s["seconds"] = round(time.time() - t0, 1)
    else:
        log = redact(Path(a.file).read_text(encoding="utf-8", errors="replace"))
        s = summarize(f"parse {a.file}", a.exit_code, log, Path(a.file), cfg["max_inline_lines"])
    print(json.dumps(s, indent=2) if a.json else render(s))
    return 0 if s["status"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
