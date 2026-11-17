"""READ-ONLY registry of official, published programs (free allowances, trials, developer/education/startup credits, grants).
Lists and validates entries from config/credits.yaml; flags expiry and staleness. It never applies, claims, refers or farms anything,
and a credit counts as usable ONLY when status == confirmed with a provider confirmation reference and is not expired."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import core  # noqa: F401
from aos_lib import load_config

REQUIRED = ("provider", "source_url", "eligibility", "last_checked", "status")


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def list_programs(root: Path | None = None, today: date | None = None) -> list[dict]:
    today = today or date.today()
    cfg = load_config("credits", root)
    stale_days = cfg.get("stale_after_days", 90)
    out = []
    for pid, p in (cfg.get("programs") or {}).items():
        p = p or {}
        problems = [f"missing {k}" for k in REQUIRED if not p.get(k)]
        if p.get("source_url") and not str(p["source_url"]).startswith("https://"):
            problems.append("source_url must be an https official page")
        if p.get("status") not in ("published", "applied", "confirmed", None):
            problems.append("status must be published|applied|confirmed")
        if p.get("status") == "confirmed" and not p.get("provider_confirmation"):
            problems.append("confirmed requires provider_confirmation (reference from the provider)")
        exp, chk = _d(p.get("expires")), _d(p.get("last_checked"))
        expired = bool(exp and exp < today)
        stale = bool(chk is None or (today - chk).days > stale_days)
        usable = p.get("status") == "confirmed" and bool(p.get("provider_confirmation")) and not expired and not problems
        out.append({"id": pid, "provider": p.get("provider"), "source_url": p.get("source_url"), "eligibility": p.get("eligibility"), "status": p.get("status"),
                    "expires": str(p["expires"]) if p.get("expires") else None, "days_left": (exp - today).days if exp and not expired else None,
                    "expired": expired, "stale": stale, "usable_as_credit": usable, "problems": problems,
                    "advice": ("expired" if expired else "re-check the official page: info is stale" if stale else "eligibility shown as published; AOS does not apply on your behalf")})
    return out


def usable(root: Path | None = None) -> list[dict]:
    return [p for p in list_programs(root) if p["usable_as_credit"]]
