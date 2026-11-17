#!/usr/bin/env python3
"""dormakaba policy guard: fails (exit 1) if a disabled feature returns, a forbidden provider kind is enabled,
a non-AI-Platform model is listed, programmatic model switching is enabled, or the company name is capitalized.

  policy_guard.py [--root AOS_DIR]
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
from aos_lib import AOS_DIR, parse_yaml

BAD_NAME = re.compile(r"\b(Dormakaba|DORMAKABA)\b")


def check(aos_dir: Path = AOS_DIR) -> list[str]:
    errs = []
    rd = lambda n: parse_yaml((aos_dir / n).read_text(encoding="utf-8"))
    pol = rd("config/dormakaba-policy.yaml")
    for f in pol.get("forbidden_files") or []:
        if (aos_dir / f).exists():
            errs.append(f"disabled feature present: {f}")
    for name, p in (rd("config/providers.yaml").get("providers") or {}).items():
        p = p or {}
        if p.get("kind") in (pol.get("forbidden_provider_kinds") or []):
            errs.append(f"forbidden provider kind {p.get('kind')!r}: {name}")
        if p.get("requires_approval") and p.get("enabled") is True and not p.get("approved_by"):
            errs.append(f"provider {name} enabled without approved_by")
    models = rd("config/models.yaml")
    if models.get("can_switch_model_programmatically"):
        errs.append("automatic model switching must stay false")
    for m in models.get("available") or []:
        if (models.get("models") or {}).get(m, {}).get("provider") != "ai-platform":
            errs.append(f"model {m} is not an AI Platform model")
    files = [p for d in ("skills", "agents", "docs", "commands") if (aos_dir / d).exists() for p in (aos_dir / d).rglob("*.md")]
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if BAD_NAME.search(line):
                errs.append(f"{f.relative_to(aos_dir)}:{i}: company name must be lowercase 'dormakaba'")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--root")
    a = ap.parse_args(argv)
    errs = check(Path(a.root) if a.root else AOS_DIR)
    print("policy: OK" if not errs else f"policy: {len(errs)} violation(s)")
    for e in errs:
        print(" -", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
