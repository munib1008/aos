import json
import unittest

import helpers  # noqa: F401
import aos_lib
import route_task


class YamlAndRedaction(unittest.TestCase):
    def test_yaml_subset(self):
        d = aos_lib.parse_yaml("a: 1\nb:\n  c: [x, y]\n  d: {1: 5, k: true}  # c\n  e:\n    - u\n    - v\nf: null\n")
        self.assertEqual(d, {"a": 1, "b": {"c": ["x", "y"], "d": {1: 5, "k": True}, "e": ["u", "v"]}, "f": None})

    def test_all_configs_load(self):
        for n in ("models", "optimization", "security", "projects"):
            self.assertTrue(aos_lib.load_config(n))

    def test_redaction(self):
        for s in ("key sk-abcdefghijklmnop1234", "ghp_" + "a" * 30, "password = hunter22222", "Authorization: Bearer abcdefghijklmnopqrstu",
                  "postgres://u:secretpw@host/db", "AKIA" + "A" * 16):
            self.assertTrue(aos_lib.contains_secret(s), s)
        self.assertFalse(aos_lib.contains_secret("plain refactor of tokenizer module"))


class Router(unittest.TestCase):
    def test_trivial_is_level1_small_model(self):
        r = route_task.route("fix typo in login label")
        self.assertEqual((r["level"], r["model"], r["effort"]), (1, "claude-haiku-5-5", "low"))

    def test_security_override_forces_level4_with_approval(self):
        r = route_task.route("add cross-tenant isolation to payment api")
        self.assertEqual(r["level"], 4)
        self.assertTrue(r["requires_human_approval"])

    def test_scores_override_and_clamp(self):
        r = route_task.route("fix typo", scores_override={"data_integrity": 3})
        self.assertEqual(r["level"], 4)
        r = route_task.route("fix typo", scores_override={"scope": 99})
        self.assertEqual(r["scores"]["scope"], 3)

    def test_respects_available_models(self):
        r = route_task.route("add cross-tenant isolation", available=["claude-sonnet-5-5"])
        self.assertEqual(r["model"], "claude-sonnet-5-5")
        self.assertIn("unavailable", r["model_note"])
        r = route_task.route("x", available=["unknown-model"])
        self.assertIsNone(r["model"])

    def test_no_programmatic_switch_claim(self):
        r = route_task.route("fix typo", current_model="claude-opus-5-5")
        self.assertIn("client switching not assumed", r["action"])

    def test_escalation_and_provider_note(self):
        base = route_task.route("add endpoint for report export")
        esc = route_task.route("add endpoint for report export", failures=2)
        self.assertEqual(esc["level"], min(4, base["level"] + 1))
        self.assertIsNotNone(route_task.route("anything", failures=4)["provider_note"])
        self.assertIsNone(route_task.route("anything", failures=1)["provider_note"])


if __name__ == "__main__":
    unittest.main()
