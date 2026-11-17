import json
import unittest
from pathlib import Path

import helpers
import summarize_tests as st

UNITTEST_FAIL = """..F
======================================================================
FAIL: test_x (mod.T.test_x)
----------------------------------------------------------------------
Traceback (most recent call last):
AssertionError: 1 != 2

Ran 3 tests in 0.01s

FAILED (failures=1)
"""
PYTEST = "=== 1 failed, 4 passed in 0.5s ===\nFAILED tests/test_a.py::test_one - assert 0\n"
JEST = "Tests:       2 failed, 5 passed, 7 total\n  ● Login › rejects bad pw\n"


class Summaries(unittest.TestCase):
    def test_parse_formats(self):
        p = st.parse(UNITTEST_FAIL)
        self.assertEqual((p["total"], p["failed"], p["passed"]), (3, 1, 2))
        self.assertIn("test_x (mod.T.test_x)", p["failures"])
        p = st.parse(PYTEST)
        self.assertEqual((p["failed"], p["passed"]), (1, 4))
        self.assertIn("tests/test_a.py::test_one", p["failures"])
        p = st.parse(JEST)
        self.assertEqual((p["failed"], p["passed"], p["total"]), (2, 5, 7))

    def test_failure_never_reported_as_passed(self):
        self.assertEqual(st.summarize("c", 1, UNITTEST_FAIL, None, 40)["status"], "FAILED")
        self.assertEqual(st.summarize("c", 2, "weird crash output", None, 40)["status"], "FAILED (unparsed)")
        self.assertEqual(st.summarize("c", 0, "Ran 2 tests in 0.1s\n\nOK", None, 40)["status"], "PASSED")
        # exit 0 but failures printed => still FAILED
        self.assertEqual(st.summarize("c", 0, UNITTEST_FAIL, None, 40)["status"], "FAILED")

    def test_run_saves_full_log_and_exit_code(self):
        root = helpers.make_repo({"a.txt": "x"})
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = st.main(["--root", str(root), "--json", "run", "--", "python3", "-c", "import sys;print('Ran 1 tests in 0s');print('password=hunter2222222');sys.exit(3)"])
        self.assertEqual(code, 1)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["exit_code"], 3)
        log = Path(out["full_log"]).read_text()
        self.assertNotIn("hunter2222222", log)               # redacted
        self.assertTrue(Path(out["full_log"]).exists())


if __name__ == "__main__":
    unittest.main()
