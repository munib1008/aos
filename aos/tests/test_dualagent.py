import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
import dualagent as D

OWN = """# Module Ownership

## Codex integration authority

Codex owns and reviews changes to:

- `apps/api/**`
- `packages/contracts/**`
- `packages/database/**`
- commercial, approval, transaction infrastructure
- all root governance documents and final integration decisions

## Claude Code ownership

Claude Code may own isolated implementation branches for:

- `engines/invisible-market/**`
- `engines/waste-to-wealth/**`
- research-backed provider adapters that implement an approved shared interface

Claude must consume `@nexus/contracts` and emit `EngineCandidateSchema`.

## Integration protocol

1. One small feature branch; see `docs/x.md`.
"""


def repo():
    r = helpers.make_repo({"MODULE_OWNERSHIP.md": OWN, "README.md": "x", "packages/contracts/a.ts": "1", "engines/invisible-market/a.ts": "1"}, name="nx")
    subprocess.run(["git", "branch", "baseline"], cwd=r, check=True)
    return r


def commit(r, files: dict, msg="c"):
    for k, v in files.items():
        p = r / k; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(v)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], cwd=r, check=True)


class Ownership(unittest.TestCase):
    def test_parse_real_format(self):
        own = D.parse_ownership(OWN)
        self.assertEqual(own["codex"], ["apps/api/**", "packages/contracts/**", "packages/database/**"])
        self.assertEqual(own["claude"], ["engines/invisible-market/**", "engines/waste-to-wealth/**"])     # @nexus/contracts and docs/x.md (other section) ignored

    def test_owner_of(self):
        own = D.parse_ownership(OWN)
        for path, want in [("apps/api/src/app.ts", "codex"), ("packages/contracts/src/a.ts", "codex"), ("engines/invisible-market/src/x/y.ts", "claude"), ("README.md", "codex"),
                           ("./MODULE_OWNERSHIP.md", "codex"), ("docs/x.md", None), ("engines/other/a.ts", None), ("apps/web/page.tsx", None)]:
            self.assertEqual(D.owner_of(path, own), want, path)

    def test_glob_semantics(self):
        self.assertTrue(D._rx("a/**/b.ts").match("a/b.ts")); self.assertTrue(D._rx("a/**/b.ts").match("a/x/y/b.ts"))
        self.assertFalse(D._rx("a/*.ts").match("a/x/b.ts")); self.assertTrue(D._rx("a/*.ts").match("a/b.ts"))


class Check(unittest.TestCase):
    def test_pass_fail_unowned(self):
        r = repo()
        subprocess.run(["git", "checkout", "-qb", "claude/im"], cwd=r, check=True)
        commit(r, {"engines/invisible-market/new.ts": "2"})
        self.assertTrue(D.check(r, "claude", "baseline")["ok"])
        commit(r, {"packages/contracts/a.ts": "changed"})
        res = D.check(r, "claude", "baseline")
        self.assertFalse(res["ok"]); self.assertEqual(res["violations"], ["packages/contracts/a.ts"])
        self.assertTrue(D.check(r, "codex", "baseline")["violations"] == ["engines/invisible-market/new.ts"])   # symmetrical
        commit(r, {"docs/loose.md": "x"})
        self.assertIn("docs/loose.md", D.check(r, "claude", "baseline")["unowned"])
        r2 = repo(); subprocess.run(["git", "checkout", "-qb", "c/x"], cwd=r2, check=True); commit(r2, {"docs/loose.md": "x"})
        self.assertFalse(D.check(r2, "claude", "baseline")["ok"]); self.assertTrue(D.check(r2, "claude", "baseline", allow_unowned=True)["ok"])

    def test_missing_ownership_file_fails_closed(self):
        r = helpers.make_repo({"a.txt": "1"}, name="noown"); subprocess.run(["git", "branch", "b"], cwd=r, check=True)
        self.assertFalse(D.check(r, "claude", "b")["ok"])

    def test_ownership_read_from_base_branch(self):
        r = repo(); subprocess.run(["git", "checkout", "-qb", "claude/x"], cwd=r, check=True)
        (r / "MODULE_OWNERSHIP.md").unlink()
        commit(r, {"engines/invisible-market/z.ts": "1"})
        res = D.check(r, "claude", "baseline")                     # ownership still read from the base branch; deleting it is itself a violation (root file = integrator's)
        self.assertFalse(res["ok"]); self.assertIn("MODULE_OWNERSHIP.md", res["violations"])


class Hook(unittest.TestCase):
    def test_blocks_other_agents_files_by_role(self):
        r = repo(); (r / ".aos" / "dual").mkdir(parents=True); (r / ".aos/dual/role").write_text("claude")
        env = os.environ.pop("AOS_AGENT_ROLE", None)
        try:
            blocked = D.hook(r, {"tool_name": "Edit", "tool_input": {"file_path": str(r / "packages/contracts/a.ts")}})
            self.assertEqual(blocked[0], 2); self.assertIn("owned by codex", blocked[1])
            self.assertEqual(D.hook(r, {"tool_name": "Write", "tool_input": {"file_path": str(r / "engines/invisible-market/n.ts")}})[0], 0)
            self.assertEqual(D.hook(r, {"tool_name": "Bash", "tool_input": {"command": "rm packages/contracts/a.ts"}})[0], 0)      # documented limit
            self.assertEqual(D.hook(r, {"tool_name": "Edit", "tool_input": {"file_path": "/etc/hosts"}})[0], 0)
            (r / ".aos/dual/role").write_text("codex")
            self.assertEqual(D.hook(r, {"tool_name": "Edit", "tool_input": {"file_path": str(r / "engines/invisible-market/a.ts")}})[0], 2)
            os.environ["AOS_AGENT_ROLE"] = "claude"
            self.assertEqual(D.hook(r, {"tool_name": "Edit", "tool_input": {"file_path": str(r / "apps/api/x.ts")}})[0], 2)         # env wins over file
        finally:
            os.environ.pop("AOS_AGENT_ROLE", None)
            if env:
                os.environ["AOS_AGENT_ROLE"] = env

    def test_no_role_no_block_and_cli_exit_code(self):
        r = repo()
        self.assertEqual(D.hook(r, {"tool_name": "Edit", "tool_input": {"file_path": str(r / "packages/contracts/a.ts")}})[0], 0)
        (r / ".aos" / "dual").mkdir(parents=True); (r / ".aos/dual/role").write_text("claude")
        p = subprocess.run([sys.executable, str(helpers.AOS / "scripts" / "dualagent.py"), "--root", str(r), "hook"], input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(r / "apps/api/x.ts")}, "cwd": str(r)}),
                           capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "AOS_AGENT_ROLE"})
        self.assertEqual(p.returncode, 2); self.assertIn("owned by codex", p.stderr)


class Worktrees(unittest.TestCase):
    def test_init_status_and_packet(self):
        r = repo()
        res = D.init_worktree(r, "baseline", "invisible-market", "claude")
        self.assertNotIn("error", res); wt = Path(res["worktree"])
        self.assertTrue(wt.exists()); self.assertEqual((wt / ".aos/dual/role").read_text(), "claude"); self.assertEqual(res["branch"], "claude/invisible-market")
        self.assertIn("error", D.init_worktree(r, "baseline", "invisible-market", "claude"))                       # no overwrite
        commit(wt, {"engines/invisible-market/engine.ts": "x"}, "engine")
        st = {x["branch"]: x for x in D.status(r, "baseline")}
        self.assertEqual(st["claude/invisible-market"]["ahead"], 1); self.assertEqual(st["claude/invisible-market"]["role"], "claude")
        p = D.review_packet(wt, "claude", "baseline")
        t = p.read_text()
        self.assertIn("Ownership check: **PASS**", t); self.assertIn("/codex:adversarial-review --base baseline", t); self.assertIn("engine", t)
        commit(wt, {"packages/contracts/a.ts": "oops"}, "bad")
        self.assertIn("**FAIL**", D.review_packet(wt, "claude", "baseline").read_text())


if __name__ == "__main__":
    unittest.main()
