import json
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers
import measure_usage as mu
import install

AOS = helpers.AOS


def line(mid, inp, out, tools=0, cache=0):
    content = [{"type": "tool_use", "id": f"t{i}", "name": "Bash"} for i in range(tools)]
    return json.dumps({"type": "assistant", "message": {"id": mid, "model": "claude-sonnet-5-5", "content": content,
                       "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cache}}})


class Usage(unittest.TestCase):
    def test_transcript_dedupes_and_never_invents_cost(self):
        f = Path(tempfile.mkdtemp()) / "t.jsonl"
        f.write_text("\n".join([line("m1", 100, 10, 1), line("m1", 100, 50, 1), line("m2", 200, 20, 2, cache=500), "garbage", json.dumps({"type": "user"})]))
        r = mu.parse_transcript(f)
        self.assertEqual((r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["model_calls"]), (300, 70, 500, 2))
        root = helpers.make_repo({"a": "b"})
        self.assertIsNone(mu.cost(root, "claude-sonnet-5-5", r))        # prices are null in config

    def rec(self, task, variant, **kw):
        return {"task": task, "variant": variant, "input_tokens": 1000, "passed": True, "regressions": 0, **kw}

    def test_compare_verdicts(self):
        r = mu.compare([self.rec("A", "baseline")])[0]
        self.assertTrue(r["verdict"].startswith("INCOMPLETE"))
        runs = [self.rec("A", "baseline")] * 3 + [self.rec("A", "aos", input_tokens=700)] * 3
        r = mu.compare(runs)[0]
        self.assertEqual(r["input_tokens"]["delta_pct"], -30.0)
        self.assertTrue(r["verdict"].startswith("ACCEPT"))
        self.assertTrue(mu.compare(runs[:1] + runs[3:4])[0]["verdict"].startswith("PRELIMINARY"))
        bad = [self.rec("B", "baseline")] * 3 + [self.rec("B", "aos", input_tokens=100, passed=False)] * 3
        self.assertTrue(mu.compare(bad)[0]["verdict"].startswith("REJECT"))      # token savings never excuse lower pass rate
        reg = [self.rec("C", "baseline")] * 3 + [self.rec("C", "aos", regressions=1)] * 3
        self.assertTrue(mu.compare(reg)[0]["verdict"].startswith("REJECT"))


class PackageIntegrity(unittest.TestCase):
    def fm(self, p):
        t = p.read_text()
        self.assertTrue(t.startswith("---\n"), p)
        head = t.split("---\n")[1]
        return dict(l.split(": ", 1) for l in head.strip().splitlines() if ": " in l)

    def test_skills_agents_commands_wellformed(self):
        skills = sorted((AOS / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(skills), 9)
        for s in skills:
            f = self.fm(s)
            self.assertEqual(f["name"], s.parent.name)
            self.assertTrue(0 < len(f["description"]) <= 400, s)
            self.assertLess(len(s.read_text().splitlines()), 40, f"{s} should stay concise")
        agents = sorted((AOS / "agents").glob("*.md"))
        self.assertEqual(len(agents), 9)
        for a in agents:
            f = self.fm(a)
            self.assertEqual(f["name"], a.stem)
            self.assertIn(f["model"], {"inherit", "haiku", "sonnet", "opus"})
            self.assertTrue(f["tools"] and f["description"])
        skill_names = {s.parent.name for s in skills}
        for c in (AOS / "commands").glob("*.md"):
            body = c.read_text()
            self.assertTrue(any(n in body for n in skill_names), c)
        self.assertEqual({c.stem for c in (AOS / "commands").glob("*.md")},
                         {"aos-optimize", "aos-scan", "aos-route", "aos-learn", "aos-fork", "aos-resume", "aos-handoff", "aos-audit", "aos-video"})

    def test_every_script_referenced_exists_and_has_help(self):
        import re, subprocess, sys
        refs = set()
        for p in list((AOS / "skills").rglob("*.md")) + list((AOS / "agents").glob("*.md")):
            refs |= set(re.findall(r"aos/scripts/(\w+\.py)", p.read_text()))
        self.assertTrue(refs)
        for r in refs:
            self.assertTrue((AOS / "scripts" / r).exists(), r)
            if r != "aos_lib.py":
                out = subprocess.run([sys.executable, str(AOS / "scripts" / r), "--help"], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, r + out.stderr)

    def test_install_idempotent_rewrites_paths_and_preserves_user_content(self):
        target = helpers.make_repo({"AGENTS.md": "# Mine\nkeep me\n", "README.md": "x"}, name="tgt")
        for _ in range(2):
            install.main(["--target", str(target)])
        a = (target / "AGENTS.md").read_text()
        self.assertIn("keep me", a)
        self.assertEqual(a.count(install.BEGIN), 1)
        skill = (target / ".claude/skills/aos-optimize/SKILL.md").read_text()
        self.assertIn(".claude/aos/scripts/memory.py", skill)
        self.assertNotIn(" aos/scripts/", skill)
        self.assertTrue((target / ".claude/aos/scripts/route_task.py").exists())
        self.assertIn(".aos/", (target / ".gitignore").read_text().split())
        import subprocess, sys
        r = subprocess.run([sys.executable, ".claude/aos/scripts/route_task.py", "fix typo"], cwd=target, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        install.main(["--target", str(target), "--uninstall"])
        self.assertFalse((target / ".claude/skills/aos-optimize").exists())
        self.assertEqual((target / "AGENTS.md").read_text().strip(), "# Mine\nkeep me")


if __name__ == "__main__":
    unittest.main()
