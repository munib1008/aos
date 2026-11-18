import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers
import benchmark_context as bench
import build_context as bc
import export_codex
import index_repository as ix
import validate_skills as vs
import video_pipeline as vp

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


class PlanValidation(unittest.TestCase):
    def test_rejects_bad_plans(self):
        d = Path(tempfile.mkdtemp())
        bad = [{"resolution": "1279x720", "shots": [{"type": "title", "text": "x", "duration": 2}]},
               {"shots": []}, {"shots": [{"type": "gif", "duration": 2}]},
               {"shots": [{"type": "image", "src": "missing.png", "duration": 2}]},
               {"shots": [{"type": "title", "text": "x", "duration": 0.1}]},
               {"shots": [{"type": "title", "duration": 2}]}, {"fps": 500, "shots": [{"type": "title", "text": "x", "duration": 2}]}]
        for p in bad:
            with self.assertRaises(vp.PipelineError, msg=str(p)):
                vp.validate_plan(p, d)

    def test_dry_run_writes_nothing_and_never_uses_shell(self):
        d = Path(tempfile.mkdtemp())
        evil = "'; rm -rf / #\" $(touch pwned) :"
        r = vp.build({"shots": [{"type": "title", "text": evil, "duration": 2}]}, d / "o.mp4", d, dry_run=True)
        self.assertFalse((d / "o.mp4").exists())
        self.assertTrue(all(isinstance(c, list) for c in r["commands"]))
        self.assertNotIn(evil, json.dumps(r["commands"]))          # text goes via file, not into the command line


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class RealRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = vp.selftest(Path(tempfile.mkdtemp()))

    def test_selftest_passes_with_audio_and_srt(self):
        self.assertEqual(self.res["verify"]["status"], "PASSED", self.res)
        out = Path(self.res["build"]["output"])
        self.assertTrue(out.with_suffix(".srt").read_text().startswith("1\n00:00:00,000 --> 00:00:02,000"))

    def test_verify_catches_defects(self):
        out = Path(self.res["build"]["output"])
        self.assertEqual(vp.verify(out, expect_duration=20)["status"], "FAILED")
        self.assertEqual(vp.verify(out, expect_size="1920x1080")["status"], "FAILED")
        self.assertEqual(vp.verify(out.with_name("nope.mp4"))["status"], "FAILED")
        silent = out.with_name("silent.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(out), "-an", "-c:v", "copy", str(silent)], check=True)
        self.assertEqual(vp.verify(silent, require_audio=True)["status"], "FAILED")
        junk = out.with_name("junk.mp4"); junk.write_bytes(b"not a video")
        self.assertEqual(vp.verify(junk)["status"], "FAILED")

    def test_black_detection(self):
        d = Path(tempfile.mkdtemp())
        vp.build({"resolution": "320x240", "shots": [{"type": "title", "text": "x", "duration": 2, "bg": "0x000000"}]}, d / "b.mp4", d)
        self.assertEqual(vp.verify(d / "b.mp4", max_black=0.3)["status"], "FAILED")


class CodexExport(unittest.TestCase):
    def test_export_valid_and_stripped(self):
        t = Path(tempfile.mkdtemp())
        r = export_codex.export(t)
        self.assertEqual(r["errors"], [])
        self.assertGreaterEqual(len(r["skills"]), 14)       # granular export tool still available for advanced use
        for s in (t / ".codex/skills").glob("*/SKILL.md"):
            txt = s.read_text()
            self.assertNotIn("allowed-tools", txt); self.assertNotIn("\ntools:", txt); self.assertNotIn("$ARGUMENTS", txt)
            self.assertLessEqual(len(txt.encode()), 8192)
            self.assertEqual(vs.validate_skill(s), [])
        try:
            import tomllib
        except ImportError:
            return
        self.assertEqual([f.name for f in (t / ".codex/agents").glob("*.toml")], ["aos.toml"])
        d = tomllib.loads((t / ".codex/agents/aos.toml").read_text())
        self.assertEqual(d["name"], "aos"); self.assertEqual(d["sandbox_mode"], "workspace-write")     # Bash/Edit/Write: not read-only
        self.assertIn("developer_instructions", d)

    def test_spec_validator_catches_violations(self):
        d = Path(tempfile.mkdtemp())
        for name, fm in {"Bad_Name": "name: Bad_Name\ndescription: x", "ok-name": "name: other\ndescription: x", "empty": "name: empty\ndescription:",
                         "dbl--hy": "name: dbl--hy\ndescription: x", "long": "name: long\ndescription: " + "x" * 1025}.items():
            (d / name).mkdir(); (d / name / "SKILL.md").write_text(f"---\n{fm}\n---\nbody\n")
        errs = vs.validate_dir(d)
        for key in ("Bad_Name", "ok-name", "empty", "dbl--hy", "long"):
            self.assertTrue(any(e.startswith(key) for e in errs), key)
        (d / "nofm").mkdir(); (d / "nofm/SKILL.md").write_text("no frontmatter")
        self.assertTrue(any("frontmatter" in e for e in vs.validate_dir(d)))


class Benchmark(unittest.TestCase):
    def test_runs_on_fixture_and_reports_honest_fields(self):
        root = helpers.make_repo(helpers.SAMPLE, name="bench")
        tasks = [{"query": "fix revoke token", "expected": ["src/auth/token_refresh.py"], "symbol": "revoke_token"}]
        r = bench.run(root, tasks, 1, 1, 3, 2)
        self.assertEqual(r["aos"]["file_recall"], 1.0)
        self.assertEqual(r["aos"]["definition_coverage"], 1.0)     # target symbol is at the END of a 130-line file: must still be in the snippet
        self.assertTrue(r["caveats"])
        self.assertLess(r["aos"]["mean_tokens"], r["naive"]["mean_tokens"] + 1)

    def test_generated_tasks_deterministic(self):
        root = helpers.make_repo(helpers.SAMPLE, name="bench2")
        idx = ix.build(root)
        self.assertEqual(bench.gen_tasks(root, idx, 5, 3), bench.gen_tasks(root, idx, 5, 3))

    def test_symbol_deep_in_large_file_is_included(self):
        files = {"big.py": "\n".join(f"def helper_function_{i}(x):\n    return x + {i}\n" for i in range(120)) + "\ndef compute_refund_total(order):\n    return 42\n"}
        root = helpers.make_repo(files, name="deep")
        out = bc.build(root, "fix compute refund total", level=2)
        self.assertIn("def compute_refund_total", out["text"])


if __name__ == "__main__":
    unittest.main()
