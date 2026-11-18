import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import AOS

if not (AOS / "packaging").exists():
    raise unittest.SkipTest("packaging tests run in the source repo only")
sys.path.insert(0, str(AOS / "packaging"))
import package  # noqa: E402


class Dist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp()) / "dist"
        cls.zips = package.build_all(cls.out)

    def names(self, z):
        return set(zipfile.ZipFile(z).namelist())

    def test_eight_zips_checksums_and_verification(self):
        self.assertEqual(len(self.zips), 8)
        self.assertTrue((self.out / "SHA256SUMS").exists() and (self.out / "README.md").exists())
        for line in (self.out / "SHA256SUMS").read_text().splitlines():
            h, n = line.split("  ")
            self.assertEqual(hashlib.sha256((self.out / n).read_bytes()).hexdigest(), h)
        errs = [e for z in self.zips for e in package.verify_zip(z)]
        self.assertEqual(errs, [])

    def test_reproducible(self):
        second = package.build_all(Path(tempfile.mkdtemp()) / "d2")
        for a, b in zip(self.zips, second):
            self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(), hashlib.sha256(b.read_bytes()).hexdigest(), a.name)

    def test_noscript_has_no_code_and_skill_form_is_valid(self):
        for prof in ("default", "dormakaba"):
            n = self.names(self.out / f"aos-{prof}-skill-noscript.zip")
            self.assertFalse(any(x.endswith(".py") for x in n)); self.assertIn("aos/SKILL.md", n); self.assertIn("aos/procedures.md", n); self.assertIn("aos/modules/roles/omega.md", n)
            s = self.names(self.out / f"aos-{prof}-skill.zip")
            self.assertIn("aos/scripts/route_task.py", s); self.assertIn("aos/modules/agents/aos.md", s)

    def test_profiles_do_not_leak(self):
        for z in self.zips:
            n = self.names(z)
            if "-default-" in z.name:
                self.assertFalse(any("dormakaba" in x.lower() or "model-advisor" in x for x in n), z.name)
                self.assertTrue(any("tccm.py" in x or "aos-token-capacity" in x for x in n) or "noscript" in z.name, z.name)
            else:
                for bad in ("core/", "tccm.py", "aos-token-capacity", "aos-local-inference", "aos-model-routing", "credits.yaml", "compute.yaml", "routing.yaml"):
                    self.assertFalse(any(bad in x for x in n), (z.name, bad))
                self.assertTrue(any("dormakaba-policy.yaml" in x for x in n), z.name)

    def test_project_bundles_layout(self):
        c = self.names(self.out / "aos-default-claude-code.zip")
        for need in (".claude/skills/aos/SKILL.md", ".claude/skills/aos/modules/skills/aos-omega.md", ".claude/skills/aos/modules/references/humanizer/references/patterns.md", ".claude/agents/aos.md", ".claude/commands/aos.md",
                     ".claude/aos/scripts/memory.py", "AGENTS.md", "CLAUDE.md", "AOS-INSTALL.md"):
            self.assertIn(need, c)
        self.assertEqual({x.split("/")[2] for x in c if x.startswith(".claude/skills/")}, {"aos"})
        self.assertEqual({x for x in c if x.startswith(".claude/agents/")}, {".claude/agents/aos.md"})
        x = self.names(self.out / "aos-default-codex.zip")
        for need in (".codex/skills/aos/SKILL.md", ".codex/agents/aos.toml", ".codex/aos/scripts/memory.py", "AGENTS.md"):
            self.assertIn(need, x)
        self.assertEqual({y for y in x if y.startswith(".codex/agents/")}, {".codex/agents/aos.toml"})

    def test_dormakaba_stage_passes_its_own_test_suite(self):
        st = package.stage("dormakaba", Path(tempfile.mkdtemp()))
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(st / "tests")], cwd=st, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])

    def test_global_install_uses_home_paths(self):
        import install, os
        home = Path(tempfile.mkdtemp())
        old = os.environ.get("HOME"); os.environ["HOME"] = str(home)
        try:
            Path.home()  # noqa
            install.main(["--global", "--harness", "claude"])
        finally:
            os.environ["HOME"] = old or ""
        t = (home / ".claude/skills/aos/modules/skills/aos-optimize.md").read_text()
        self.assertIn("~/.claude/aos/scripts/memory.py", t)
        self.assertTrue((home / ".claude/aos/scripts/memory.py").exists())

    def test_master_agent_everywhere_and_global_install(self):
        import install, os
        for z in self.zips:
            n = self.names(z)
            if "claude-code" in z.name:
                self.assertIn(".claude/agents/aos.md", n); self.assertIn(".claude/aos/scripts/omega_runner.py", n)
                self.assertIn(".claude/skills/aos/modules/references/aos-omega/references/MASTER_PROMPT.md", n)
            if "codex" in z.name:
                self.assertIn(".codex/agents/aos.toml", n); self.assertIn(".codex/skills/aos/modules/references/aos-omega/references/CONTINUE_PROMPT.md", n)
            if z.name.endswith("-skill.zip"):
                self.assertIn("aos/modules/agents/aos.md", n); self.assertIn("aos/scripts/omega.py", n); self.assertIn("aos/modules/references/aos-omega/references/CONTINUE_PROMPT.md", n)
            if "noscript" in z.name:
                self.assertIn("aos/modules/agents/aos.md", n)
        dk = self.names(self.out / "aos-dormakaba-claude-code.zip")
        self.assertIn(".claude/skills/aos/modules/references/aos-omega/references/domain-service-360.md", dk)
        self.assertFalse(any("domain-service-360" in x for x in self.names(self.out / "aos-default-claude-code.zip")))
        home = Path(tempfile.mkdtemp()); old = os.environ.get("HOME"); os.environ["HOME"] = str(home)
        try:
            install.main(["--global", "--harness", "both"])
        finally:
            os.environ["HOME"] = old or ""
        t = (home / ".claude/agents/aos.md").read_text()
        self.assertIn("~/.claude/aos/scripts/omega.py", t); self.assertNotIn(" aos/scripts/omega.py", t)
        self.assertIn("~/.claude/aos/scripts/", (home / ".claude/skills/aos/SKILL.md").read_text())
        self.assertTrue((home / ".claude/aos/scripts/omega_runner.py").exists()); self.assertTrue((home / ".codex/agents/aos.toml").exists())
        r = subprocess.run([sys.executable, str(home / ".claude/aos/scripts/omega.py"), "--root", str(home), "status"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_profile_install_bakes_overlay(self):
        import install
        from helpers import make_repo
        tgt = make_repo({"README.md": "x"}, name="dkp")
        install.main(["--target", str(tgt), "--harness", "both", "--profile", "dormakaba"])
        self.assertEqual((tgt / ".claude/aos/config/PROFILE").read_text().strip(), "dormakaba")
        self.assertFalse((tgt / ".claude/aos/core").exists()); self.assertFalse((tgt / ".claude/skills/aos/modules/skills/aos-model-routing.md").exists())
        self.assertTrue((tgt / ".claude/skills/aos/modules/skills/model-advisor.md").exists()); self.assertTrue((tgt / ".codex/skills/aos/modules/references/model-advisor/references/model-catalog.md").exists())


if __name__ == "__main__":
    unittest.main()
