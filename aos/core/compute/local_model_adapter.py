"""Adapter for LOCAL inference engines. Ollama, llama.cpp (`llama-server`) and vLLM all expose an OpenAI-compatible
/v1 API, so one stdlib HTTP client covers them (no engine is bundled or re-implemented).

Security: loopback/private hosts only unless allow_remote=True (so "local" can never silently send code off-machine);
local traffic bypasses any configured HTTP(S) proxy.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

import core  # noqa: F401
from aos_lib import AOS_DIR  # noqa: F401

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never route local calls through the agent proxy
ENGINE_BINARIES = {"ollama": ["ollama"], "llama.cpp": ["llama-server", "llama-cli"], "vllm": ["vllm"]}


class LocalAdapterError(RuntimeError):
    pass


def is_local_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str | None = None, api_key: str | None = None, timeout: float = 120, allow_remote: bool = False):
        if not allow_remote and not is_local_url(base_url):
            raise LocalAdapterError(f"refusing non-local base_url {base_url!r} for a local adapter (set allow_remote=True to override)")
        self.base, self.model, self.key, self.timeout = base_url.rstrip("/"), model, api_key, timeout

    def _req(self, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.key}"} if self.key else {})})
        try:
            with _OPENER.open(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise LocalAdapterError(f"{self.base}{path}: {e}") from e

    def list_models(self, timeout: float = 2.0) -> list[str]:
        j = self._req("/models", timeout=timeout)
        return sorted(m.get("id") or m.get("name") for m in j.get("data", []) if isinstance(m, dict))

    def chat(self, prompt: str | None = None, messages: list[dict] | None = None, max_tokens: int = 512, temperature: float = 0.0, system: str | None = None) -> dict:
        if not self.model:
            raise LocalAdapterError("model not set")
        msgs = messages or ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt or ""}]
        t0 = time.time()
        j = self._req("/chat/completions", {"model": self.model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature, "stream": False})
        lat = time.time() - t0
        try:
            text = j["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LocalAdapterError(f"unexpected response shape: {str(j)[:200]}") from e
        u = j.get("usage") or {}
        return {"text": text, "input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens"), "latency_s": round(lat, 3),
                "usage_reported": bool(u), "model": j.get("model", self.model)}


def detect_engines(endpoints: dict[str, str] | None = None, probe_timeout: float = 1.0) -> list[dict]:
    """Installed binaries + live endpoints + served models. Endpoints default to providers.yaml local entries."""
    if endpoints is None:
        from aos_lib import load_config
        endpoints = {(p.get("engine") or n): p["base_url"] for n, p in (load_config("providers").get("providers") or {}).items() if p.get("kind") == "local" and p.get("base_url")}
    out = []
    for engine, url in endpoints.items():
        installed = any(shutil.which(b) for b in ENGINE_BINARIES.get(engine, [engine]))
        running, models, err = False, [], None
        try:
            models = OpenAICompatClient(url).list_models(timeout=probe_timeout)
            running = True
        except LocalAdapterError as e:
            err = str(e)[:120]
        out.append({"engine": engine, "base_url": url, "installed": installed, "running": running, "models": models, "error": None if running else err})
    return out
