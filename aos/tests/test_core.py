import json
import unittest

import helpers  # noqa: F401
import aos_lib
import route_task


class YamlAndRedaction(unittest.TestCase):
    def test_yaml_subset(self):
        d = aos_lib.parse_yaml("a: 1\nb:\n  c: [x, y]\n  d: {1: 5, k: true}  # c\n  e:\n    - u\n    - v\nf: null\n")
        self.assertEqual(d, {"a": 1, "b": {"c": ["x", "y"], "d": {1: 5, "k": True}, "e": ["u", "v"]}, "f": None})

    def test_nested_inline_collections(self):
        d = aos_lib.parse_yaml("m: {tier: balanced, price: {input: 3, output: 15.5}, tags: [a, b], q: 'x, y'}\nl: [[1, 2], {k: v}]\n")
        self.assertEqual(d["m"], {"tier": "balanced", "price": {"input": 3, "output": 15.5}, "tags": ["a", "b"], "q": "x, y"})
        self.assertEqual(d["l"], [[1, 2], {"k": "v"}])

    def test_profile_overlay_replaces_config(self):
        import os
        if not (aos_lib.AOS_DIR / "profiles" / "dormakaba").exists():
            self.skipTest("profile overlay lives in the source repo; baked builds have it applied already")
        os.environ["AOS_PROFILE"] = "dormakaba"
        try:
            self.assertEqual(aos_lib.active_profile(), "dormakaba")
            self.assertIn("gpt-6-luna", aos_lib.load_config("models")["available"])
        finally:
            del os.environ["AOS_PROFILE"]
        self.assertNotIn("gpt-6-luna", aos_lib.load_config("models")["available"])

    def test_all_configs_load(self):
        for n in ("models", "optimization", "security", "projects", "routing", "compute", "providers", "budgets", "credits", "skills", "privacy"):
            if aos_lib.config_path(n).exists():          # a profile build may omit features (e.g. TCCM configs)
                self.assertTrue(aos_lib.load_config(n), n)
        for n in ("models", "optimization", "security", "providers", "budgets", "skills", "privacy"):
            self.assertTrue(aos_lib.config_path(n).exists(), n)

    def test_redaction(self):
        for s in ("key sk-abcdefghijklmnop1234", "ghp_" + "a" * 30, "password = hunter22222", "Authorization: Bearer abcdefghijklmnopqrstu",
                  "postgres://u:secretpw@host/db", "AKIA" + "A" * 16):
            self.assertTrue(aos_lib.contains_secret(s), s)
        self.assertFalse(aos_lib.contains_secret("plain refactor of tokenizer module"))
        for benign in ("input_tokens=a.input_tokens", "saved_tokens_est=(e.get(", "Tokens = chars/4", "Secrets: refused", "token_count = self.count(", "max_tokens: 4000", "api_key_env: ANTHROPIC_API_KEY"):
            self.assertFalse(aos_lib.contains_secret(benign), benign)
        for real in ("password: hunter22222", "API_KEY=abcd1234efgh", "client_secret = 'Zx9.Q7w-1234'", "auth_token: P@ss.w0rd123"):
            self.assertTrue(aos_lib.contains_secret(real), real)


class Router(unittest.TestCase):
    def test_trivial_is_level1_small_model(self):
        r = route_task.route("fix typo in login label")
        cfg = aos_lib.load_config("models")
        self.assertEqual((r["level"], r["effort"]), (1, "low"))
        self.assertIn(r["model"], cfg["tier_preference"]["small"])          # profile-agnostic: smallest tier of the active catalog

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
