"""Compute capacity inventory + local-model recommendations.

Detects RAM, CPU, GPU/VRAM, storage, acceleration, local engines and served models, then recommends model SIZE CLASSES that fit
(memory estimate = params_B * bytes/param + overhead; an estimate, not a measurement) and which task categories they suit.
Parsers are pure functions over text so they are unit-testable without hardware.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

import core  # noqa: F401
from aos_lib import atomic_write, load_config, run, state_dir
from core.compute import local_model_adapter as lma


def parse_meminfo(text: str) -> dict:
    kv = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(\w+):\s+(\d+) kB", text, re.M)}
    return {"total_gb": round(kv.get("MemTotal", 0) / 1048576, 2), "available_gb": round(kv.get("MemAvailable", kv.get("MemFree", 0)) / 1048576, 2)}


def parse_cpuinfo(text: str) -> dict:
    model = re.search(r"model name\s*:\s*(.+)", text)
    flags = set((re.search(r"flags\s*:\s*(.+)", text) or [None, ""])[1].split())
    feats = [f for f in ("avx2", "avx512f", "avx512_vnni", "fma", "amx_tile") if f in flags]
    return {"model": model.group(1).strip() if model else None, "features": feats}


def parse_nvidia_smi(csv: str) -> list[dict]:
    gpus = []
    for line in csv.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and re.match(r"\d+", parts[1]):
            gpus.append({"vendor": "nvidia", "name": parts[0], "vram_total_gb": round(int(re.match(r"\d+", parts[1]).group()) / 1024, 2), "vram_used_gb": round(int(re.match(r"\d+", parts[2]).group()) / 1024, 2)})
    return gpus


def hardware_inventory() -> dict:
    sysname = platform.system()
    ram = {"total_gb": 0, "available_gb": 0}
    cpu = {"model": platform.processor() or None, "cores": os.cpu_count(), "features": []}
    if sysname == "Linux":
        try:
            ram = parse_meminfo(Path("/proc/meminfo").read_text())
            cpu.update(parse_cpuinfo(Path("/proc/cpuinfo").read_text()))
        except OSError:
            pass
    elif sysname == "Darwin":
        rc, o = run(["sysctl", "-n", "hw.memsize"])
        if rc == 0 and o.strip().isdigit():
            ram = {"total_gb": round(int(o.strip()) / 2**30, 2), "available_gb": None}
        rc, o = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        cpu["model"] = o.strip() if rc == 0 else cpu["model"]
    gpus = []
    if shutil.which("nvidia-smi"):
        rc, o = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"])
        if rc == 0:
            gpus += parse_nvidia_smi(o)
    if shutil.which("rocm-smi"):
        gpus.append({"vendor": "amd", "name": "AMD GPU (rocm-smi present; query VRAM manually)", "vram_total_gb": None, "vram_used_gb": None})
    accel = []
    if any(g["vendor"] == "nvidia" for g in gpus):
        accel.append("cuda")
    if any(g["vendor"] == "amd" for g in gpus):
        accel.append("rocm")
    if sysname == "Darwin" and platform.machine() == "arm64":
        accel.append("metal (unified memory)")
    accel += [f for f in cpu.get("features", []) if f.startswith("avx") or f == "amx_tile"]
    du = shutil.disk_usage(str(Path.home()))
    return {"os": f"{sysname} {platform.release()}", "arch": platform.machine(), "ram": ram, "cpu": cpu, "gpus": gpus,
            "acceleration": accel or ["cpu-generic"], "storage_free_gb": round(du.free / 2**30, 1)}


def model_memory_gb(params_b: float, quant: str, cfg: dict) -> float:
    lm = cfg["local_models"]
    return round(params_b * lm["quant_bytes_per_param"][quant] + lm["overhead_gb"], 2)


def recommend_local(hw: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or {**load_config("routing"), **load_config("compute")}
    lm = cfg["local_models"]
    vram = max([g["vram_total_gb"] or 0 for g in hw["gpus"]] or [0])
    unified = any("metal" in a for a in hw["acceleration"])
    ram_total = hw["ram"].get("available_gb") or hw["ram"].get("total_gb") or 0
    if vram:
        budget, where = vram * lm["vram_fraction_for_model"], "gpu"
    elif unified:
        budget, where = ram_total * lm["vram_fraction_for_model"], "unified-memory gpu"
    else:
        budget, where = ram_total * lm["ram_fraction_for_model"], "cpu (ram)"
    fits = []
    for size in lm["size_classes_b"]:
        for q in ("q8", "q4"):
            need = model_memory_gb(size, q, cfg)
            if need <= budget:
                fits.append({"size_b": size, "quant": q, "est_memory_gb": need, "tier": lm["tier_by_size"][size]})
                break
    best = fits[-1] if fits else None
    suit = cfg["task_categories"]["local_suitable"]
    cats = sorted(c for c, mn in suit.items() if best and best["size_b"] >= mn)
    warn = []
    if where.startswith("cpu"):
        warn.append("CPU-only: generation speed is bound by memory bandwidth; measure tokens/s with benchmark before relying on it for interactive work")
    if best is None:
        warn.append("no size class fits: use deterministic tools and cloud models; local inference not recommended on this machine")
    if hw["storage_free_gb"] < 20:
        warn.append("less than 20 GB free disk for model files")
    return {"memory_budget_gb": round(budget, 2), "placement": where, "fits": fits, "recommended": best, "suitable_categories": cats,
            "not_for": ["security_critical (needs independent verification by a stronger model)", "multi-module design", "anything without tests to verify"],
            "families_to_consider": lm["families_to_consider"],
            "note": "Verify current model releases, licences (some restrict commercial use) and quantization availability before downloading. Sizes are estimates.",
            "warnings": warn}


def inventory(root: Path | None = None, probe: bool = True) -> dict:
    cfg = {**load_config("routing", root), **load_config("compute", root)}
    hw = hardware_inventory()
    inv = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hardware": hw, "engines": lma.detect_engines() if probe else [],
           "local_recommendation": recommend_local(hw, cfg)}
    prov = (load_config("providers", root).get("providers") or {})
    inv["providers"] = [{"id": k, "kind": v["kind"], "enabled": v.get("enabled"), "models": sorted((v.get("models") or {}).keys())} for k, v in prov.items()]
    if root:
        atomic_write(state_dir(root) / "capacity.json", json.dumps(inv, indent=1))
    return inv


def sample_resources() -> dict:
    """Current local memory/GPU use for the ledger (best effort)."""
    out = {}
    try:
        mi = parse_meminfo(Path("/proc/meminfo").read_text())
        out["ram_used_gb"] = round(mi["total_gb"] - mi["available_gb"], 2)
    except OSError:
        pass
    if shutil.which("nvidia-smi"):
        rc, o = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"])
        if rc == 0:
            g = parse_nvidia_smi(o)
            if g:
                out["gpu_mem_used_gb"], out["gpu_mem_total_gb"] = g[0]["vram_used_gb"], g[0]["vram_total_gb"]
    return out
