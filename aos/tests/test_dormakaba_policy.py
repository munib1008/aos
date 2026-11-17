"""Policy-profile tests. In the source repo they build the dormakaba profile into a temp dir; inside a baked dormakaba distribution
(config/PROFILE present) they test that tree directly. Skipped when neither applies."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import AOS
import aos_lib
from policy_guard import check
from validate_skills import validate_dir
from aos_lib import parse_yaml

PROFILE_FILE = AOS / "config" / "PROFILE"


def target():
    if (AOS / "packaging").exists():
        sys.path.insert(0, str(AOS / "packaging"))
        import package
        return package.stage("dormakaba", Path(tempfile.mkdtemp())), True
    if PROFILE_FILE.exists() and PROFILE_FILE.read_text().strip() == "dormakaba":
        return AOS, False
    return None, False


def run_script(root: Path, script: str, *args: str) -> str:
    env = {**os.environ}
    env.pop("AOS_PROFILE", None)
    r = subprocess.run([sys.executable, str(root / "scripts" / script), *args], cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


class PolicyProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.staged = target()
        if cls.root is None:
            raise unittest.SkipTest("not the source repo and not a dormakaba distribution")

    def test_policy_guard_clean(self):
        self.assertEqual(check(self.root), [])

    def test_disabled_and_excluded_features_absent(self):
        pol = parse_yaml((self.root / "config" / "dormakaba-policy.yaml").read_text())
        for f in pol["forbidden_files"]:
            self.assertFalse((self.root / f).exists(), f)
        for f in ("core", "scripts/tccm.py", "skills/aos-token-capacity", "skills/aos-local-inference", "skills/aos-model-routing", "config/routing.yaml", "config/compute.yaml", "config/credits.yaml"):
            self.assertFalse((self.root / f).exists(), f)

    def test_guard_detects_violations(self):
        import shutil
        d = Path(tempfile.mkdtemp()) / "aos"
        shutil.copytree(self.root, d, ignore=shutil.ignore_patterns("__pycache__"))
        (d / "core" / "compute").mkdir(parents=True)
        p = d / "config" / "providers.yaml"
        p.write_text(p.read_text() + "  sneaky:\n    kind: api_free\n    enabled: true\n")
        m = d / "config" / "models.yaml"
        m.write_text(m.read_text().replace("can_switch_model_programmatically: false", "can_switch_model_programmatically: true"))
        (d / "docs").mkdir(exist_ok=True); (d / "docs" / "x.md").write_text("Dormakaba capitalised\n")
        errs = "\n".join(check(d))
        for needle in ("core/compute", "api_free", "switching", "lowercase"):
            self.assertIn(needle, errs)

    def test_skills_valid_and_merged(self):
        self.assertEqual(validate_dir(self.root / "skills"), [])
        for n in ("model-advisor", "humanizer", "aos-database", "aos-security-review", "aos-video"):
            self.assertTrue((self.root / "skills" / n / "SKILL.md").exists(), n)
        self.assertTrue((self.root / "skills/model-advisor/references/model-catalog.md").exists())
        self.assertTrue((self.root / "skills/humanizer/references/patterns.md").exists())
        for f in (self.root / "skills").rglob("*.md"):
            self.assertNotIn("<!-- profile:", f.read_text(), f)

    def test_models_are_platform_models_only(self):
        m = parse_yaml((self.root / "config" / "models.yaml").read_text())
        self.assertFalse(m["can_switch_model_programmatically"])
        self.assertTrue(all(m["models"][x]["provider"] == "ai-platform" for x in m["available"]))
        self.assertNotIn("claude-haiku-5-5", m["available"])

    def test_routing_uses_platform_catalog(self):
        light = json.loads(run_script(self.root, "route_task.py", "fix typo in readme", "--json"))
        self.assertEqual((light["level"], light["model"]), (1, "gpt-6-luna"))
        crit = json.loads(run_script(self.root, "route_task.py", "production database migration that touches payment ledger", "--json"))
        self.assertEqual((crit["level"], crit["model"]), (4, "claude-opus-5-5")); self.assertTrue(crit["requires_human_approval"])
        self.assertGreaterEqual(json.loads(run_script(self.root, "route_task.py", "add schema migration for orders table", "--json"))["level"], 2)

    def test_skill_selection(self):
        s = json.loads(run_script(self.root, "select_skills.py", "add oauth token endpoint", "--json"))["skills"]
        self.assertIn("aos-security-review", s); self.assertIn("aos-api-development", s)
        self.assertIn("aos-database", json.loads(run_script(self.root, "select_skills.py", "add schema migration and index for orders table", "--json"))["skills"])

    def test_access_control_data_redacted(self):
        for s in ["key system number: 12345678", "Card code = 99887766", "TAN code: 554433", "security answer is Fluffy"]:
            self.assertTrue(aos_lib.contains_secret(s), s)
            self.assertIn("[REDACTED", aos_lib.redact(s))

    def test_company_name_lowercase_everywhere(self):
        import re
        for f in self.root.rglob("*.md"):
            self.assertIsNone(re.search(r"\b(Dormakaba|DORMAKABA)\b", f.read_text(encoding="utf-8")), f)


if __name__ == "__main__":
    unittest.main()
