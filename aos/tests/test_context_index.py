import json
import unittest
from pathlib import Path

import helpers
import build_context
import index_repository as ix
import memory as mem


class IndexAndContext(unittest.TestCase):
    def setUp(self):
        self.root = helpers.make_repo(helpers.SAMPLE, name="ctxproj")

    def test_index_incremental(self):
        first = ix.build(self.root)
        self.assertEqual(first["stats"]["reused"], 0)
        second = ix.build(self.root)
        self.assertEqual(second["stats"]["parsed"], 0)
        self.assertEqual(second["stats"]["reused"], first["stats"]["files"])
        p = self.root / "src/billing/invoice.py"
        p.write_text(p.read_text() + "\ndef extra():\n    pass\n")
        helpers.subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        third = ix.build(self.root)
        self.assertEqual(third["stats"]["parsed"], 1)

    def test_stack_symbols_tests(self):
        idx = ix.build(self.root)
        self.assertIn("node", idx["stack"])
        self.assertIn("tests/test_token_refresh.py", idx["tests"])
        names = {s[0] for s in idx["files"]["src/auth/token_refresh.py"]["symbols"]}
        self.assertTrue({"refresh_token", "revoke_token"} <= names)

    def test_query_finds_file_symbol_and_test(self):
        res = ix.query(ix.build(self.root), "fix refresh token bug")
        self.assertEqual(res[0]["path"], "src/auth/token_refresh.py")
        self.assertIn("tests/test_token_refresh.py", res[0]["tests"])
        self.assertEqual(ix.query(ix.build(self.root), "zzzz"), [])

    def test_refs_rejects_injection(self):
        with self.assertRaises(ValueError):
            ix.refs(self.root, "x; rm -rf /")
        self.assertTrue(ix.refs(self.root, "refresh_token"))

    def test_context_uses_snippets_not_whole_file_and_stays_in_budget(self):
        out = build_context.build(self.root, "fix refresh token bug", "refresh works", level=2)
        text = out["text"]
        self.assertIn("## P0 Task", text)
        self.assertIn("def refresh_token", text)
        self.assertNotIn("def filler_50", text)            # big file is excerpted
        self.assertLessEqual(out["est_tokens"], out["budget"])

    def test_invariants_survive_tiny_budget_and_untrusted_wrapper(self):
        mem.add(self.root, "ctxproj", "decision", "Never log refresh tokens", "README.md", "verified", tags=["invariant", "security"])
        out = build_context.build(self.root, "fix refresh token bug", level=1)
        self.assertIn("Never log refresh tokens", out["text"])
        self.assertIn('untrusted="true"', out["text"])

    def test_low_priority_omitted_before_high(self):
        for i in range(8):
            mem.add(self.root, "ctxproj", "verified_solution", f"refresh token lesson {i} " + "y" * 450, "README.md", "verified")
        out = build_context.build(self.root, "fix refresh token bug", level=1)
        self.assertTrue(out["omitted"] or out["est_tokens"] <= out["budget"])
        self.assertIn("## P2", out["text"])


if __name__ == "__main__":
    unittest.main()
