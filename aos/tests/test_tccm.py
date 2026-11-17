import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import helpers
import fakeserver
import core  # noqa: F401
from core.benchmark import model_quality as mq
from core.budget import usage_dashboard as ud, usage_monitor as um
from core.cache import context_cache as cc
from core.compute import capacity_manager as cm, credits_registry as cr, local_model_adapter as lma
from core.budget import capacity_plan as cp
from core.routing import model_selector as ms, provider_switch as ps


def repo(overrides: dict[str, str] | None = None):
    r = helpers.make_repo({"a.py": "x=1", "README.md": "hi"})
    for name, text in (overrides or {}).items():
        p = r / ".aos" / "config" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return r


PAID = """providers:
  paid-x:
    kind: api_paid
    enabled: true
    data_policy: no_training
    models:
      mid-model: {tier: balanced, price_per_mtok: {input: 3, output: 15}}
      top-model: {tier: strongest, price_per_mtok: {input: 15, output: 75}}
"""


class Hardware(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(cm.parse_meminfo("MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n"), {"total_gb": 16.0, "available_gb": 8.0})
        c = cm.parse_cpuinfo("model name\t: Test CPU\nflags\t: fpu avx2 fma avx512f\n")
        self.assertEqual(c["model"], "Test CPU"); self.assertIn("avx2", c["features"])
        g = cm.parse_nvidia_smi("NVIDIA RTX 4090, 24564, 1024\n")
        self.assertEqual(g[0]["vram_total_gb"], 23.99); self.assertEqual(g[0]["vendor"], "nvidia")

    def hw(self, ram=16, vram=None, accel=("avx2",), disk=100):
        return {"ram": {"total_gb": ram, "available_gb": ram}, "gpus": [{"vendor": "nvidia", "name": "g", "vram_total_gb": vram, "vram_used_gb": 0}] if vram else [], "acceleration": list(accel), "storage_free_gb": disk}

    def test_recommendations_scale_with_hardware(self):
        small = cm.recommend_local(self.hw(ram=4)); mid = cm.recommend_local(self.hw(ram=16)); big = cm.recommend_local(self.hw(ram=64, vram=48))
        self.assertLess(small["recommended"]["size_b"], mid["recommended"]["size_b"]); self.assertLess(mid["recommended"]["size_b"], big["recommended"]["size_b"])
        self.assertEqual(big["placement"], "gpu"); self.assertTrue(any("CPU-only" in w for w in mid["warnings"]))
        self.assertIn("security_critical", " ".join(mid["not_for"]))

    def test_nothing_fits_says_so(self):
        r = cm.recommend_local(self.hw(ram=1))
        self.assertIsNone(r["recommended"]); self.assertTrue(any("no size class fits" in w for w in r["warnings"]))
        self.assertTrue(any("disk" in w for w in cm.recommend_local(self.hw(disk=5))["warnings"]))

    def test_live_inventory_runs(self):
        inv = cm.inventory(None, probe=False)
        self.assertIn("ram", inv["hardware"]); self.assertIn("local_recommendation", inv)


class Adapter(unittest.TestCase):
    def test_refuses_remote_and_accepts_loopback(self):
        with self.assertRaises(lma.LocalAdapterError):
            lma.OpenAICompatClient("https://api.example.com/v1", "m")
        lma.OpenAICompatClient("http://127.0.0.1:11434/v1", "m"); lma.OpenAICompatClient("http://192.168.1.5:8080/v1", "m")
        self.assertFalse(lma.is_local_url("http://8.8.8.8/v1"))

    def test_chat_models_usage_and_proxy_bypass(self):
        srv, url, st = fakeserver.serve(lambda p, n: "pong")
        old = os.environ.get("HTTP_PROXY"); os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"          # a bad proxy must not be used for local traffic
        try:
            c = lma.OpenAICompatClient(url, "fake-7b")
            self.assertEqual(c.list_models(), ["fake-7b"])
            r = c.chat("ping")
            self.assertEqual(r["text"], "pong"); self.assertTrue(r["usage_reported"]); self.assertGreaterEqual(r["latency_s"], 0)
            eng = lma.detect_engines({"ollama": url}); self.assertTrue(eng[0]["running"]); self.assertEqual(eng[0]["models"], ["fake-7b"])
        finally:
            srv.shutdown()
            os.environ.pop("HTTP_PROXY", None) if old is None else os.environ.__setitem__("HTTP_PROXY", old)
        down = lma.detect_engines({"ollama": "http://127.0.0.1:1/v1"})
        self.assertFalse(down[0]["running"]); self.assertTrue(down[0]["error"])


class Budget(unittest.TestCase):
    def test_default_authorizes_nothing_and_unknown_price_needs_auth(self):
        r = repo()
        self.assertEqual(um.check(r, "paid_api", 0.001)["decision"], "needs_authorization")
        self.assertEqual(um.check(r, "paid_api", None)["decision"], "needs_authorization")
        self.assertEqual(um.check(r, "subscription", None)["decision"], "allow")
        self.assertEqual(um.check(r, "local", None)["decision"], "allow")

    def test_ceilings_spend_and_authorization(self):
        r = repo({"budgets.yaml": "paid_api:\n  per_task_usd: 1\n  daily_usd: 2\n  monthly_usd: 10\n"})
        self.assertEqual(um.check(r, "paid_api", 0.5)["decision"], "allow")
        self.assertEqual(um.check(r, "paid_api", 1.5)["decision"], "needs_authorization")
        um.record(r, kind="paid_api", provider="p", model="m", task="t", cost_usd_actual=1.8)
        self.assertEqual(um.check(r, "paid_api", 0.5)["decision"], "needs_authorization")          # day ceiling 2
        um.authorize(r, 1.0, "day", "human ok")
        self.assertEqual(um.check(r, "paid_api", 0.5)["decision"], "allow")
        with self.assertRaises(ValueError):
            um.authorize(r, 0, "day", "x")

    def test_mode_ceiling_is_stricter_of_the_two(self):
        r = repo({"budgets.yaml": "paid_api:\n  per_task_usd: 5\n  daily_usd: 5\n  monthly_usd: 5\nmodes:\n  local_first:\n    daily_usd: 1\n"})
        self.assertEqual(um.ceilings(r, "local_first")["daily_usd"], 1); self.assertEqual(um.ceilings(r, "balanced")["daily_usd"], 5)

    def test_kinds_never_mixed_and_cost_basis_labelled(self):
        r = repo()
        um.record(r, kind="paid_api", provider="a", model="m", task="t", input_tokens=10, output_tokens=5, cost_usd_actual=0.5)
        um.record(r, kind="paid_api", provider="a", model="m", task="t", cost_usd_estimated=0.25)
        um.record(r, kind="paid_api", provider="a", model="m", task="t")                              # unpriced
        um.record(r, kind="subscription", provider="s", model="m", task="t", input_tokens=1000)
        um.record(r, kind="local", provider="l", model="m", task="t", input_tokens=500)
        sp = um.spend(r, "paid_api")
        self.assertEqual((sp["all_usd"], sp["unpriced_entries"]), (0.75, 1)); self.assertEqual(sp["estimated_share"], 0.333)
        self.assertEqual(um.spend(r, "subscription")["all_usd"], 0.0)
        with self.assertRaises(ValueError):
            um.record(r, kind="local", provider="l", model="m", task="t", cost_usd_actual=0.1)
        with self.assertRaises(ValueError):
            um.record(r, kind="bitcoin", provider="x", model="m", task="t")

    def test_windows(self):
        r = repo()
        um.record(r, kind="paid_api", provider="a", model="m", task="t", cost_usd_actual=1, ts="2026-10-31T10:00:00Z")
        um.record(r, kind="paid_api", provider="a", model="m", task="t", cost_usd_actual=2, ts="2026-11-10T10:00:00Z")
        s = um.spend(r, "paid_api", datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertEqual((s["day_usd"], s["month_usd"], s["all_usd"]), (0, 2, 3))

    def test_cost_estimate_with_cache(self):
        self.assertIsNone(um.estimate_cost(None, 100, 100))
        self.assertEqual(um.estimate_cost({"input": 10, "output": 20, "cache_read": 1}, 1_000_000, 1_000_000, 500_000), 5 + 0.5 + 20)


class Selector(unittest.TestCase):
    L = {"local-ollama": ["qwen-coder:7b"]}

    def test_deterministic_first(self):
        r = ms.select("find the file that handles login", root=repo())
        self.assertEqual(r["decision"], "deterministic"); self.assertIn("index_repository", r["tool"])

    def test_categories(self):
        for t, c in [("fix typo in login label", "small_change"), ("classify these tickets", "classification"), ("explain what this does", "code_explanation"),
                     ("add cross-tenant isolation", "security_critical"), ("summarize the failing test log", "test_summary")]:
            self.assertEqual(ms.categorize(t), c, t)

    def test_modes_pick_expected_kinds(self):
        r = repo()
        lf = ms.select("classify these support tickets", root=r, mode="local_first", local_models=self.L)["choice"]
        bal = ms.select("classify these support tickets", root=r, mode="balanced", local_models=self.L)["choice"]
        self.assertEqual(lf["kind"], "local"); self.assertEqual(bal["model"], "claude-haiku-5-5")      # strongest model must not win trivial work
        self.assertNotEqual(ms.select("classify these support tickets", root=r, mode="quality_first", local_models=self.L)["choice"]["model"], "claude-fable-5-2")

    def test_critical_work_never_local_and_not_below_floor(self):
        r = repo()
        s = ms.select("add cross-tenant isolation to the payment api", root=r, mode="local_first", local_models=self.L)
        self.assertNotEqual(s["choice"]["kind"], "local"); self.assertIn(s["choice"]["tier"], ("strong", "strongest")); self.assertIn("verification", s["verification"])
        self.assertTrue(any("security-critical" in e["reason"] or "level" in e["reason"] for e in s["excluded"] if "qwen" in e["model"]))
        q = ms.select("add cross-tenant isolation to the payment api", root=r, mode="quality_first")
        self.assertEqual(q["choice"]["model"], "claude-fable-5-2")

    def test_level_floor_for_features(self):
        self.assertGreaterEqual(ms.select("implement a new export endpoint", root=repo())["level"], 2)

    def test_small_local_model_excluded_for_code_changes(self):
        s = ms.select("fix typo in the label", root=repo(), local_models={"local-ollama": ["tiny-1b"]})
        self.assertTrue(any("below" in e["reason"] for e in s["excluded"]))

    def test_confidential_excludes_unverified_cloud(self):
        r = repo()
        s = ms.select("classify these tickets", root=r, confidential=True, local_models=self.L)
        self.assertEqual(s["choice"]["kind"], "local")
        s2 = ms.select("classify these tickets", root=r, confidential=True)
        self.assertEqual(s2["decision"], "no_candidate")

    def test_free_tier_needs_verified_current_terms(self):
        base = "providers:\n  free-x:\n    kind: api_free\n    enabled: true\n    data_policy: no_training\n    commercial_use_allowed: true\n"
        mdl = "    models:\n      free-model: {tier: balanced, price_per_mtok: null}\n"
        s = ms.select("classify tickets", root=repo({"providers.yaml": base + mdl}))
        self.assertTrue(any("not verified" in e["reason"] for e in s["excluded"]))
        old = repo({"providers.yaml": base + "    terms_verified_on: 2020-01-01\n" + mdl})
        self.assertTrue(any("re-verify" in e["reason"] for e in ms.select("classify tickets", root=old)["excluded"]))
        ok = repo({"providers.yaml": base + f"    terms_verified_on: {date.today().isoformat()}\n" + mdl})
        self.assertEqual(ms.select("classify tickets", root=ok)["choice"]["kind"], "free_tier")
        nc = repo({"providers.yaml": base.replace("commercial_use_allowed: true", "commercial_use_allowed: null") + f"    terms_verified_on: {date.today().isoformat()}\n" + mdl})
        self.assertTrue(any("commercial" in e["reason"] for e in ms.select("classify tickets", root=nc)["excluded"]))
        self.assertNotEqual(ms.select("classify tickets", root=nc, commercial=False)["choice"]["kind"], "x")

    def test_paid_never_silent(self):
        r = repo({"providers.yaml": PAID.replace("enabled: true", "enabled: true"), "budgets.yaml": "paid_api:\n  per_task_usd: 0\n  daily_usd: 0\n  monthly_usd: 0\n"})
        # disable subscription so paid is the only option
        r2 = repo({"providers.yaml": PAID + "  claude-subscription:\n    kind: subscription\n    enabled: false\n    models: {}\n"})
        s = ms.select("implement a new export endpoint", root=r2)
        self.assertEqual(s["decision"], "needs_authorization"); self.assertEqual(s["choice"]["kind"], "paid_api")
        r3 = repo({"providers.yaml": PAID + "  claude-subscription:\n    kind: subscription\n    enabled: false\n    models: {}\n", "budgets.yaml": "paid_api:\n  per_task_usd: 5\n  daily_usd: 5\n  monthly_usd: 5\n"})
        self.assertEqual(ms.select("implement a new export endpoint", root=r3)["decision"], "execute")

    def test_alternative_offered_when_blocked(self):
        r = repo({"providers.yaml": PAID, "budgets.yaml": "paid_api:\n  per_task_usd: 0\n"})
        s = ms.select("implement a new export endpoint", root=r, mode="quality_first")
        if s["decision"] == "needs_authorization":
            self.assertIsNotNone(s["alternative_without_authorization"])

    def test_measured_stats_override_priors(self):
        r = repo()
        before = ms.select("classify tickets", root=r)["choice"]; self.assertIn("prior", before["p_source"])
        for i in range(6):
            um.record(r, kind="subscription", provider="claude-subscription", model=before["model"], task="t", category="classification", success=i < 2)
        after = ms.select("classify tickets", root=r)
        top = next(x for x in [after["choice"]] + after["escalation_path"] if x["model"] == before["model"])
        self.assertTrue(top["p_source"].startswith("measured")); self.assertAlmostEqual(top["p_success"], 0.333, 2)

    def test_escalation_requires_new_evidence(self):
        s = ms.select("implement a new export endpoint", root=repo())
        self.assertFalse(ms.next_escalation(s, "")["escalate"])
        self.assertFalse(ms.next_escalation(s, "tests fail: X", ["tests fail: X"])["escalate"])
        e = ms.next_escalation(s, "tests fail: Y"); self.assertTrue(e["escalate"] and e["next"])

    def test_default_config_enables_no_paid_provider(self):
        from aos_lib import load_config
        for pid, p in load_config("providers")["providers"].items():
            if p["kind"] in ("api_paid", "api_free"):
                self.assertFalse(p["enabled"], pid)
        self.assertEqual(load_config("budgets")["paid_api"]["monthly_usd"], 0)


class Cache(unittest.TestCase):
    def setUp(self):
        self.r = helpers.make_repo({"a.py": "x=1"})

    def key(self, fp, prompt="hi", temp=0):
        return cc.make_key("p", "m", prompt, {"temperature": temp}, fp)

    def test_hit_then_invalidate_on_code_change(self):
        fp = cc.state_fingerprint(self.r); k = self.key(fp)
        self.assertTrue(cc.put(self.r, k, "hi", "answer", fingerprint=fp, params={"temperature": 0}, provider="p", model="m")["stored"])
        self.assertEqual(cc.get(self.r, k, fp)["response"], "answer")
        (self.r / "a.py").write_text("x=2")
        fp2 = cc.state_fingerprint(self.r)
        self.assertNotEqual(fp, fp2); self.assertIsNone(cc.get(self.r, self.key(fp2), fp2)); self.assertIsNone(cc.get(self.r, k, fp2))   # old key + new state: miss
        (self.r / "new_untracked.py").write_text("y=1")
        self.assertNotEqual(fp2, cc.state_fingerprint(self.r))

    def test_policy_change_invalidates(self):
        fp = cc.state_fingerprint(self.r)
        p = self.r / ".aos" / "config" / "security.yaml"; p.parent.mkdir(parents=True); p.write_text("version: 2\n")
        self.assertNotEqual(fp, cc.state_fingerprint(self.r))

    def test_refuses_secrets_confidential_nondeterministic(self):
        fp = cc.state_fingerprint(self.r)
        self.assertIn("secret", cc.put(self.r, "k1", "use sk-abcdefghijklmnop1234", "x", fingerprint=fp, params={"temperature": 0}, provider="p", model="m")["reason"])
        self.assertIn("secret", cc.put(self.r, "k2", "hi", "token=abcdef123456789", fingerprint=fp, params={"temperature": 0}, provider="p", model="m")["reason"])
        self.assertIn("confidential", cc.put(self.r, "k3", "hi", "x", fingerprint=fp, params={"temperature": 0}, provider="p", model="m", confidential=True)["reason"])
        self.assertIn("non-deterministic", cc.put(self.r, "k4", "hi", "x", fingerprint=fp, params={"temperature": 0.7}, provider="p", model="m")["reason"])
        self.assertEqual(cc.stats(self.r)["entries"], 0)
        self.assertEqual(cc.stats(self.r)["refused"], 4)

    def test_ttl_and_clear(self):
        fp = cc.state_fingerprint(self.r); k = self.key(fp)
        cc.put(self.r, k, "hi", "a", fingerprint=fp, params={"temperature": 0}, provider="p", model="m", ttl_s=-1)
        self.assertIsNone(cc.get(self.r, k, fp))
        cc.put(self.r, k, "hi", "a", fingerprint=fp, params={"temperature": 0}, provider="p", model="m")
        self.assertEqual(cc.clear(self.r), 1)

    def test_duplicate_calls_and_prefix(self):
        fp = cc.state_fingerprint(self.r)
        self.assertFalse(cc.check_duplicate(self.r, "grep", {"q": "x"}, fp)["duplicate"])
        self.assertTrue(cc.check_duplicate(self.r, "grep", {"q": "x"}, fp)["duplicate"])
        self.assertFalse(cc.check_duplicate(self.r, "grep", {"q": "y"}, fp)["duplicate"])
        (self.r / "a.py").write_text("x=3")
        self.assertFalse(cc.check_duplicate(self.r, "grep", {"q": "x"}, cc.state_fingerprint(self.r))["duplicate"])   # state changed: not a duplicate
        self.assertEqual(cc.stable_prefix_ratio(["SYSTEM RULES abc task1", "SYSTEM RULES abc task2"]), round(len("SYSTEM RULES abc task") / len("SYSTEM RULES abc task1"), 3))
        self.assertEqual(cc.stable_prefix_ratio(["a"]), 0.0)


def answer_table(good: bool):
    def f(prompt: str, n: int) -> str:
        p = prompt.lower()
        if "ticket" in p and "classify" in p:
            return ("bug" if "crashes" in p else "feature" if "dark mode" in p else "question") if good else "bug"
        if "leftpad" in p:
            return '{"name": "leftpad", "version": "1.3.2"}' if good else '{"name": "leftpad"}'
        if "gradle" in p:
            return "The Gradle plugin was in a subproject; declaring it in the root fixed it." if good else "It broke then it was fixed."
        if "sorted(set" in p:
            return "Returns the unique elements sorted." if good else "It prints something."
        if "slugify" in p:
            return "```python\nimport re\ndef slugify(s):\n    return re.sub(r'[^a-z0-9]+','-',s.lower()).strip('-')\n```" if good else "def slugify(s): return s"
        if "merge_intervals" in p:
            return "```python\ndef merge_intervals(iv):\n    out=[]\n    for a,b in sorted(iv):\n        if out and a<=out[-1][1]:\n            out[-1][1]=max(out[-1][1],b)\n        else:\n            out.append([a,b])\n    return out\n```" if good else "def merge_intervals(iv): return iv"
        if "parse_duration" in p:
            return "```python\nimport re\ndef parse_duration(s):\n    m={'h':3600,'m':60,'s':1}\n    return sum(int(n)*m[u] for n,u in re.findall(r'(\\d+)([hms])',s))\n```" if good else "def parse_duration(s): return 0"
        return ""
    return f


class Benchmark(unittest.TestCase):
    def test_graders(self):
        T = lambda g: {"grader": g}
        self.assertTrue(mq.grade(T({"type": "regex", "pattern": "^bug$", "flags": "i"}), " Bug ")[0])
        self.assertFalse(mq.grade(T({"type": "json_fields", "fields": {"a": 1}}), "no json")[0])
        self.assertTrue(mq.grade(T({"type": "json_fields", "fields": {"a": 1}}), 'x {"a": "1"} y')[0])
        self.assertTrue(mq.grade(T({"type": "python_tests", "tests": "assert f(2)==4"}), "```python\ndef f(x): return x*2\n```")[0])
        self.assertFalse(mq.grade(T({"type": "python_tests", "tests": "assert f(2)==4"}), "def f(x): return 0")[0])
        ok, why = mq.run_python_tests("while True: pass", "", timeout=2); self.assertFalse(ok); self.assertEqual(why, "timeout")
        self.assertFalse(mq.run_python_tests("import os; os.environ['SECRET']", "assert os.environ['SECRET']", 5)[0])          # env is empty

    def test_good_vs_bad_model_end_to_end_with_cost_per_success(self):
        r = repo(); suite = mq.load_suite(); rows = []
        for name, good, price in (("strong", True, {"input": 10, "output": 50}), ("weak", False, None)):
            srv, url, st = fakeserver.serve(answer_table(good))
            try:
                kind = "paid_api" if price else "local"
                rows += mq.run_target(r, {"name": name, "kind": kind, "base_url": url, "model": "m", "price_per_mtok": price}, suite, 1, 2)
            finally:
                srv.shutdown()
        s = {x["target"]: x for x in mq.summarize(rows)}
        self.assertEqual(s["strong"]["completion_rate"], 1.0); self.assertEqual(s["strong"]["first_try_rate"], 1.0)
        self.assertLess(s["weak"]["completion_rate"], 0.5); self.assertGreater(s["weak"]["mean_attempts"], 1)
        self.assertIsNotNone(s["strong"]["cost_per_success_usd"]); self.assertGreater(s["strong"]["cost_per_success_usd"], 0)
        self.assertIsNone(s["weak"]["cost_per_success_usd"]); self.assertIn("unknown", s["weak"]["cost_note"] + "unknown") 
        self.assertEqual(len(um.entries(r)), len(rows))                 # benchmark runs are recorded in the ledger
        self.assertTrue((r / ".aos/benchmarks/model_quality.jsonl").exists())

    def test_missing_usage_is_flagged_estimated_and_unreachable_target_fails_cleanly(self):
        srv, url, st = fakeserver.serve(lambda p, n: "bug", report_usage=False)
        try:
            rows = mq.run_target(None, {"name": "n", "kind": "local", "base_url": url, "model": "m"}, {"tasks": [mq.load_suite()["tasks"][0]]}, 1, 1, record=False)
        finally:
            srv.shutdown()
        self.assertTrue(rows[0]["tokens_estimated"])
        down = mq.run_target(None, {"name": "d", "kind": "local", "base_url": "http://127.0.0.1:1/v1", "model": "m"}, {"tasks": [mq.load_suite()["tasks"][0]]}, 1, 1, record=False)
        self.assertFalse(down[0]["success"]); self.assertIn("call failed", down[0]["note"])


class Dashboard(unittest.TestCase):
    def test_sections_separate_and_escaped(self):
        r = repo()
        um.record(r, kind="paid_api", provider="a", model="m", task="t", input_tokens=100, output_tokens=50, cost_usd_actual=0.5, success=True)
        um.record(r, kind="subscription", provider="s", model="<script>alert(1)</script>", task="t", input_tokens=9999, cached_tokens=5000, success=False, escalated_from="s/haiku")
        um.record(r, kind="local", provider="l", model="m", task="t", input_tokens=700, resources={"ram_used_gb": 6.5, "gpu_mem_used_gb": 4.0}, success=True, latency_s=3.0)
        d = ud.build(r)
        self.assertEqual(d["sections"]["paid_api"]["tokens_total"], 150); self.assertEqual(d["sections"]["subscription"]["tokens_total"], 9999)
        self.assertEqual(d["sections"]["local"]["rows"][0]["peak_gpu_gb"], 4.0)
        self.assertIsNone(d["sections"]["subscription"]["rows"][0]["cost_measured_usd"])        # subscription tokens are never priced in USD
        self.assertEqual(d["task_success_rate"], 0.667); self.assertEqual(len(d["escalations"]), 1)
        h = ud.render_html(d); self.assertNotIn("<script>alert", h); self.assertIn("&lt;script&gt;", h)
        self.assertIn("not added together", ud.render_text(d)); self.assertTrue(ud.write(r).exists())


class Planning(unittest.TestCase):
    def test_no_data_means_no_claims(self):
        r = repo(); p = cp.plan(r)
        self.assertEqual(p["measured_levers"], 0)
        self.assertTrue(all(l["evidence"] in ("no_data",) or l["lever"].startswith("Exact") for l in p["levers"]))
        self.assertIn("No aggregate saving", p["claim"])

    def test_local_offload_needs_measured_local_quality(self):
        r = repo()
        for _ in range(3):
            um.record(r, kind="subscription", provider="s", model="m", task="t", category="classification", input_tokens=1000, output_tokens=100)
        lever = [l for l in cp.plan(r)["levers"] if l["lever"].startswith("Move")][0]
        self.assertEqual(lever["evidence"], "no_data"); self.assertEqual(lever["addressable_cloud_tokens_measured"], 3300)
        qp = r / ".aos/benchmarks/model_quality.jsonl"; qp.parent.mkdir(parents=True, exist_ok=True)
        qp.write_text("\n".join(json.dumps({"kind": "local", "success": i < 3}) for i in range(4)))
        lever = [l for l in cp.plan(r)["levers"] if l["lever"].startswith("Move")][0]
        self.assertEqual(lever["evidence"], "measured"); self.assertEqual(lever["tokens_movable_if_verified"], round(3300 * 0.75))


class Credits(unittest.TestCase):
    def test_validation_expiry_and_no_self_confirmation(self):
        today = date.today().isoformat()
        r = repo({"credits.yaml": f"""programs:
  good:
    provider: example
    source_url: https://example.com/program
    eligibility: "as published"
    expires: 2099-01-01
    last_checked: {today}
    status: published
  old:
    provider: example
    source_url: https://example.com/old
    eligibility: x
    expires: 2020-01-01
    last_checked: 2020-01-01
    status: confirmed
    provider_confirmation: REF-1
  liar:
    provider: example
    source_url: http://insecure.example.com
    eligibility: x
    last_checked: {today}
    status: confirmed
  mine:
    provider: example
    source_url: https://example.com/p
    eligibility: x
    last_checked: {today}
    status: confirmed
    provider_confirmation: EMAIL-42
"""})
        by = {p["id"]: p for p in cr.list_programs(r)}
        self.assertFalse(by["good"]["usable_as_credit"]); self.assertIsNotNone(by["good"]["days_left"])
        self.assertTrue(by["old"]["expired"]); self.assertFalse(by["old"]["usable_as_credit"])
        self.assertTrue(any("provider_confirmation" in p for p in by["liar"]["problems"])); self.assertTrue(any("https" in p for p in by["liar"]["problems"])); self.assertFalse(by["liar"]["usable_as_credit"])
        self.assertTrue(by["mine"]["usable_as_credit"]); self.assertEqual([p["id"] for p in cr.usable(r)], ["mine"])

    def test_empty_default(self):
        self.assertEqual(cr.list_programs(repo()), [])


class Switch(unittest.TestCase):
    def test_handoff_without_switching_and_authorization_flag(self):
        r = repo({"providers.yaml": PAID}); import session_manager as sm
        sm.new(r, "finish export endpoint", "tests pass")
        out = ps.plan_switch(r, "claude-subscription", "5-hour limit reached")
        self.assertFalse(out["switched"]); self.assertIn("claude-subscription", out["exhausted"])
        self.assertTrue(out["needs_user_authorization"])                          # only paid candidates remain and nothing is authorized
        self.assertTrue(all(c["provider"] != "claude-subscription" for c in out["candidates"]))
        d = Path(out["handoff_dir"]); txt = (d / "SWITCH_REQUEST.md").read_text()
        self.assertIn("AUTHORIZATION REQUIRED", txt); self.assertIn("does not bypass", txt)
        for f in ("HANDOFF.md", "TASK_CONTEXT.json", "NEXT_ACTIONS.md", "TEST_RESULTS.md", "CHANGES.md"):
            self.assertTrue((d / f).exists(), f)

    def test_no_candidates_message(self):
        r = repo(); ps.mark_exhausted(r, "claude-subscription")
        out = ps.plan_switch(r, "claude-subscription", "limit")
        self.assertEqual(out["candidates"], []); self.assertIn("none eligible", (Path(out["handoff_dir"]) / "SWITCH_REQUEST.md").read_text())


if __name__ == "__main__":
    unittest.main()
