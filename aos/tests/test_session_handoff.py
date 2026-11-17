import json
import subprocess
import unittest
from pathlib import Path

import helpers
import create_handoff as ch
import memory as mem
import session_manager as sm


class Sessions(unittest.TestCase):
    def setUp(self):
        self.root = helpers.make_repo(helpers.SAMPLE, name="sess")

    def test_fork_overlap_refused_and_worktree(self):
        parent = sm.new(self.root, "refactor auth", files=["src/auth/token_refresh.py"])
        a = sm.fork(self.root, parent["session_id"], "task A", "implementation", ["src/a.py"], worktree=True)
        self.assertTrue(Path(a["worktree"]).exists())
        self.assertTrue(a["branch"].startswith("aos/"))
        self.assertEqual(a["parent_session"], parent["session_id"])
        with self.assertRaises(SystemExit):
            sm.fork(self.root, parent["session_id"], "task B", "implementation", ["src/a.py"], worktree=False)
        sm.fork(self.root, parent["session_id"], "analysis ok", "analysis", ["src/a.py"], worktree=False)  # analysis may overlap

    def test_resume_detects_drift_and_failing_tests(self):
        m = sm.new(self.root, "task")
        m["tests"].append({"command": "pytest", "status": "FAILED"})
        sm.save(self.root, m)
        (self.root / "stray.txt").write_text("x")
        m2, warn = sm.resume(self.root, m["session_id"])
        self.assertTrue(any("not recorded" in w for w in warn))
        self.assertTrue(any("FAILED" in w for w in warn))
        subprocess.run(["git", "checkout", "-q", "-b", "other"], cwd=self.root, check=True)
        self.assertTrue(any("branch changed" in w for w in sm.resume(self.root, m["session_id"])[1]))

    def test_health_policy(self):
        class A:  # noqa
            window_frac = None; large_outputs = 0; failures = 0; topic_change = False; milestone = False
        a = A()
        self.assertEqual(sm.health(self.root, a)["action"], "continue")
        a.window_frac = 0.65
        self.assertEqual(sm.health(self.root, a)["action"], "consider-compact")
        a.window_frac = 0.9
        self.assertEqual(sm.health(self.root, a)["action"], "compact")
        a.window_frac, a.failures = None, 3
        self.assertEqual(sm.health(self.root, a)["action"], "fork-analysis")

    def test_merge_check_requires_passing_tests(self):
        m = sm.new(self.root, "t")
        self.assertFalse(sm.merge_check(self.root, m["session_id"])["ready_for_human_review"])

    def test_handoff_complete_redacted_and_resumable(self):
        proj = sm.project_id(self.root)
        mem.add(self.root, proj, "decision", "Use RLS for tenant isolation", "README.md", "verified")
        m = sm.new(self.root, "add tenant filter; token=abcd1234efgh5678secret", "no cross-tenant reads")
        m["completed"] = ["schema reviewed"]; m["pending"] = ["write policy"]
        m["tests"] = [{"command": "pytest", "status": "FAILED", "total": 4, "failed": 1, "failure_ids": ["test_x"], "full_log": ".aos/artifacts/x.log"}]
        sm.save(self.root, m)
        out = ch.build(self.root, m["session_id"])
        names = {p.name for p in out.iterdir()}
        self.assertEqual(names, {"HANDOFF.md", "TASK_CONTEXT.json", "ARCHITECTURE_SUMMARY.md", "CHANGES.md", "TEST_RESULTS.md", "NEXT_ACTIONS.md"})
        blob = "\n".join(p.read_text() for p in out.iterdir())
        self.assertNotIn("abcd1234efgh5678secret", blob)
        ctx = json.loads((out / "TASK_CONTEXT.json").read_text())
        self.assertEqual(ctx["commit"], subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip())
        self.assertIn("Do not restart completed work", ctx["continuation_prompt"])
        self.assertIn("Use RLS", blob)
        self.assertIn("FAILED", (out / "TEST_RESULTS.md").read_text())          # failures stay visible
        self.assertIn("test_x", (out / "NEXT_ACTIONS.md").read_text())
        self.assertEqual(sm.load(self.root, m["session_id"])["handoff_status"], "exported")
        # resume the task from the exported context alone
        self.assertEqual(ctx["pending"], ["write policy"])
        self.assertEqual(ctx["requirements"], ["no cross-tenant reads"])

    def test_handoff_does_not_leak_other_project_memory(self):
        mem.add(self.root, "other-project", "decision", "OTHER PRIVATE DECISION", "README.md", "verified")
        out = ch.build(self.root, None)
        self.assertNotIn("OTHER PRIVATE DECISION", "\n".join(p.read_text() for p in out.iterdir()))


if __name__ == "__main__":
    unittest.main()
