import json
import unittest

import helpers
import memory as mem
import validate_memory as vm
from aos_lib import append_jsonl


class Memory(unittest.TestCase):
    def setUp(self):
        self.root = helpers.make_repo(helpers.SAMPLE)

    def add(self, **kw):
        base = dict(category="verified_fact", content="Uses package.json", source="package.json", verification="verified")
        base.update(kw)
        return mem.add(self.root, "p1", base.pop("category"), base.pop("content"), base.pop("source"), base.pop("verification"), **base)

    def test_add_search_dedupe(self):
        a = self.add(tags=["stack"])
        b = self.add(tags=["stack"])
        self.assertTrue(b.get("_dedup"))
        self.assertEqual(a["id"], b["id"])
        res = mem.search(self.root, "p1", "package json stack")
        self.assertEqual([r["id"] for r in res], [a["id"]])
        self.assertEqual(mem.search(self.root, "p1", a["id"])[0]["id"], a["id"])      # exact id lookup

    def test_ai_suggestion_cannot_be_fact(self):
        with self.assertRaises(mem.MemoryError_):
            self.add(verification="ai_suggested")
        self.add(category="hypothesis", content="maybe cache issue", verification="ai_suggested")

    def test_secret_refused(self):
        with self.assertRaises(mem.MemoryError_):
            self.add(content="db password = hunter2222222")

    def test_project_isolation(self):
        self.add(content="secret-free fact for p1")
        mem.add(self.root, "p2", "verified_fact", "fact for p2", "package.json", "verified")
        self.assertEqual(mem.search(self.root, "p2", "p1"), [])
        # tampering: foreign record inside p2's store is detected
        append_jsonl(mem.store_path(self.root, "p2"), {"id": "x", "project": "p1", "category": "hypothesis", "content": "c", "source": "s", "verification": "unverified", "confidence": "low"})
        with self.assertRaises(mem.MemoryError_):
            mem.load_state(self.root, "p2")
        self.assertTrue(any("ISOLATION" in p for p in vm.validate(self.root, "p2")))

    def test_global_only_general_categories(self):
        with self.assertRaises(mem.MemoryError_):
            mem.add(self.root, mem.GLOBAL, "verified_fact", "project specific", "package.json", "verified")
        mem.add(self.root, mem.GLOBAL, "user_preference", "Prefer concise reports", "user:2026-11-17", "user_stated")

    def test_supersede_deprecate_rollback_keep_history(self):
        a = self.add(content="Timeout is 30s")
        b = mem.add(self.root, "p1", "verified_fact", "Timeout is 60s", "package.json", "verified", supersedes=a["id"])
        active = [r["id"] for r in mem.search(self.root, "p1", "timeout")]
        self.assertEqual(active, [b["id"]])
        self.assertEqual(len(mem.search(self.root, "p1", "timeout", include_inactive=True)), 2)
        mem.set_status(self.root, "p1", b["id"], "deprecated", "wrong")
        self.assertEqual(mem.search(self.root, "p1", "timeout"), [])
        self.assertGreaterEqual(len(open(mem.store_path(self.root, "p1")).read().splitlines()), 3)

    def test_budget_limits_results(self):
        for i in range(10):
            self.add(content=f"auth rule number {i} " + "x" * 200, source="package.json")
        small = mem.search(self.root, "p1", "auth rule", limit=10, budget=250)
        self.assertLess(len(small), 10)
        self.assertGreaterEqual(len(small), 1)

    def test_render_and_validate_sources(self):
        self.add(content="Kotlin stack", tags=["architecture"])
        self.add(category="decision", content="Use WireGuard", source="README.md")
        self.add(category="confirmed_failure", content="Gradle classloader error", source="commit:" + helpers.subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip())
        files = {p.name: p.read_text() for p in mem.render(self.root, "p1")}
        self.assertIn("Kotlin stack", files["PROJECT_ARCHITECTURE.md"])
        self.assertNotIn("Kotlin stack", files["PROJECT_MEMORY.md"])
        self.assertIn("WireGuard", files["DECISIONS.md"])
        self.assertIn("classloader", files["KNOWN_FAILURES.md"])
        self.assertEqual(vm.validate(self.root, "p1", check_sources=True), [])
        self.add(content="Ghost source", source="nope/missing.py")
        self.assertTrue(any("not found" in p for p in vm.validate(self.root, "p1", check_sources=True)))


if __name__ == "__main__":
    unittest.main()
