#!/usr/bin/env python3
"""Build downloadable AOS distributions from this repo, for each policy profile.

  python3 aos/packaging/package.py build --out dist            # 8 zips + SHA256SUMS + README for profiles default and dormakaba
  python3 aos/packaging/package.py verify dist                 # unzip every zip and check it (spec, secrets, policy, smoke runs)
  python3 aos/packaging/package.py stage dormakaba OUT_DIR     # just the profile-baked source tree

Per profile (P):
  aos-P-skill.zip           ONE Agent Skill folder `aos/` (SKILL.md + modules + scripts + config): Claude Code ~/.claude/skills/aos, Codex ~/.codex/skills/aos
  aos-P-skill-noscript.zip  same without any code (claude.ai skill upload); steps are written procedures
  aos-P-claude-code.zip     project bundle: .claude/{agents,skills,commands} + runtime (+AGENTS.md/CLAUDE.md blocks) - unzip at the project root
  aos-P-codex.zip           project bundle: .codex/{skills,agents} + runtime (+AGENTS.md) - unzip at the project root
Profile `dormakaba` bakes profiles/dormakaba/*.yaml over config/, removes policy-forbidden files and default-only text blocks.
Zips are reproducible (sorted entries, fixed timestamps).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent                                   # the repo's aos/ directory
sys.path.insert(0, str(SRC / "scripts"))
sys.path.insert(0, str(SRC))
from aos_lib import contains_secret, parse_yaml     # noqa: E402
from skillpack import apply_blocks, make_skill_dir, TCCM_SKILLS, TCCM_COMMANDS  # noqa: E402,F401

PROFILES = ("default", "dormakaba")
STAMP = (2026, 11, 17, 0, 0, 0)
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".aos", "packaging", "profiles")
def _yaml(profile: str, name: str) -> dict:
    p = SRC / "profiles" / profile / name
    return parse_yaml(p.read_text(encoding="utf-8")) if p.exists() else {}


def stage(profile: str, out_root: Path) -> Path:
    """Copy aos/ to out_root/aos with the profile baked in. Returns the staged aos dir."""
    dst = out_root / "aos"
    shutil.copytree(SRC, dst, ignore=IGNORE)
    pol, build = _yaml(profile, f"{profile}-policy.yaml"), _yaml(profile, "build.yaml")
    if profile != "default":
        prof = SRC / "profiles" / profile
        for f in prof.glob("*.yaml"):
            if f.name not in ("build.yaml", f"{profile}-policy.yaml"):
                shutil.copy(f, dst / "config" / f.name)
        shutil.copy(prof / f"{profile}-policy.yaml", dst / "config" / f"{profile}-policy.yaml")
        (dst / "config" / "PROFILE").write_text(profile + "\n")
        if (prof / "skills").exists():
            shutil.copytree(prof / "skills", dst / "skills", dirs_exist_ok=True)
        if (prof / "docs").exists():
            shutil.copytree(prof / "docs", dst / "docs", dirs_exist_ok=True)
        for rel in (pol.get("forbidden_files") or []) + (build.get("exclude") or []):
            t = dst / rel
            shutil.rmtree(t) if t.is_dir() else t.unlink(missing_ok=True)
        for name in build.get("generic_only_tests") or []:
            (dst / "tests" / name).unlink(missing_ok=True)
    else:
        for rel in ("scripts/policy_guard.py", "tests/test_dormakaba_policy.py"):
            (dst / rel).unlink(missing_ok=True)
    notes = (SRC.parent / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8") if (SRC.parent / "THIRD_PARTY_NOTICES.md").exists() else ""
    extra = SRC / "profiles" / profile / "NOTICES.extra.md"
    (dst / "THIRD_PARTY_NOTICES.md").write_text(notes.rstrip() + "\n" + (("\n" + extra.read_text(encoding="utf-8")) if extra.exists() else ""), encoding="utf-8")
    for f in dst.rglob("*.md"):
        t = f.read_text(encoding="utf-8")
        if "<!-- profile:" in t:
            f.write_text(apply_blocks(t, profile), encoding="utf-8")
    return dst


# ----------------------------------------------------------------------------- project bundles
def make_project_dir(st: Path, harness: str, out: Path, profile: str) -> Path:
    sys.path.insert(0, str(st / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("aos_install_staged", st / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.install(st, out, harness, in_place=False, glob=False, dry=False, uninstall=False)
    h = "claude-code" if harness == "claude" else "codex"
    (out / "AOS-INSTALL.md").write_text(
        f"# AOS ({profile}) for {h}\n\nUnzip at the ROOT of your project (merge into existing folders). It adds `.{harness}/` and a marked AOS block in AGENTS.md"
        + (" / CLAUDE.md" if harness == "claude" else "") + ", plus `.aos/` in .gitignore.\n\n"
        + ("Claude Code: skills, agents and slash commands load automatically (/aos-optimize, /aos-route, ...). Delegation: `claude --agent orchestrator`.\n" if harness == "claude" else
           "Codex: skills in `.codex/skills`, agents in `.codex/agents/*.toml`, instructions via AGENTS.md. Verify your Codex version loads them (docs/COMPATIBILITY.md).\n")
        + f"Runtime scripts: `python3 .{harness}/aos/scripts/<name>.py --help` (Python 3.9+, standard library only).\n", encoding="utf-8")
    return out


def zip_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
            zi = zipfile.ZipInfo(f.relative_to(src).as_posix(), STAMP)
            zi.external_attr = (0o755 if f.suffix in (".py", ".sh") else 0o644) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, f.read_bytes())


def build_all(out: Path, profiles=PROFILES) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for prof in profiles:
        with tempfile.TemporaryDirectory(prefix="aos-build-") as t:
            t = Path(t)
            st = stage(prof, t / "stage")
            for name, fn in (("skill", lambda o: make_skill_dir(st, prof, False, o)), ("skill-noscript", lambda o: make_skill_dir(st, prof, True, o)),
                             ("claude-code", lambda o: make_project_dir(st, "claude", o, prof)), ("codex", lambda o: make_project_dir(st, "codex", o, prof))):
                d = t / name
                d.mkdir()
                fn(d)
                z = out / f"aos-{prof}-{name}.zip"
                zip_dir(d, z)
                made.append(z)
    (out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(z.read_bytes()).hexdigest()}  {z.name}\n" for z in made))
    (out / "README.md").write_text(DIST_README, encoding="utf-8")
    return made


DIST_README = """# AOS downloads
Pick ONE form per profile. `default` = full generic build (incl. capacity/cost routing). `dormakaba` = policy-restricted build (AI Platform catalog only; no local runtimes, free tiers, credits registry or auto model switching).

| File | Use it as | Install |
|---|---|---|
| `aos-<profile>-skill.zip` | one Agent Skill with scripts (Claude Code or Codex) | unzip so the folder is `~/.claude/skills/aos/` (or `.codex/skills/aos/`) |
| `aos-<profile>-skill-noscript.zip` | claude.ai skill upload (no code allowed) | Settings > Capabilities > Skills > upload |
| `aos-<profile>-claude-code.zip` | skills + agents + slash commands for Claude Code | unzip at the project root; run `claude --agent orchestrator` for delegation |
| `aos-<profile>-codex.zip` | skills + agents (TOML) for Codex | unzip at the project root |

Verify downloads with `sha256sum -c SHA256SUMS`. Scripts need Python 3.9+ (standard library only). Not verified here: live loading in Claude Code, Codex and claude.ai; end-to-end cost savings. See `docs/BENCHMARKS.md` and `docs/COMPATIBILITY.md` inside the zips.
"""


# ----------------------------------------------------------------------------- verification
def _text_files(d: Path):
    for p in d.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".yaml", ".json", ".py", ".toml", ".txt", ""):
            yield p


def verify_zip(z: Path) -> list[str]:
    from validate_skills import validate_skill
    errs = []
    name = z.name
    prof = "dormakaba" if "-dormakaba-" in name else "default"
    with tempfile.TemporaryDirectory(prefix="aos-verify-") as t:
        t = Path(t)
        with zipfile.ZipFile(z) as zf:
            bad = [i.filename for i in zf.infolist() if i.filename.startswith("/") or ".." in i.filename.split("/")]
            if bad:
                return [f"{name}: unsafe zip paths {bad[:3]}"]
            zf.extractall(t)
        for f in _text_files(t):
            try:
                txt = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if contains_secret(txt) and "tests" not in f.parts and f.name not in ("aos_lib.py",):
                errs.append(f"{name}: secret-like text in {f.relative_to(t)}")
            if f.suffix != ".py" and prof == "default" and re.search(r"dormakaba|AI Platform|Frontify", txt, re.I):
                errs.append(f"{name}: dormakaba-internal text in {f.relative_to(t)}")
            if f.suffix != ".py" and "<!-- profile:" in txt:
                errs.append(f"{name}: unprocessed profile marker in {f.relative_to(t)}")
        if name.endswith("-skill.zip") or name.endswith("-skill-noscript.zip"):
            sk = t / "aos" / "SKILL.md"
            errs += validate_skill(sk)
            for need in ("modules/skills/aos-optimize.md", "config/models.yaml", "templates/HANDOFF.md"):
                if not (t / "aos" / need).exists():
                    errs.append(f"{name}: missing {need}")
            if name.endswith("-noscript.zip"):
                if any(t.rglob("*.py")):
                    errs.append(f"{name}: contains code but must be script-free")
                if any("aos/scripts" in f.read_text(encoding="utf-8") for f in t.rglob("*.md")):
                    errs.append(f"{name}: references scripts that are not bundled")
                if not (t / "aos" / "procedures.md").exists():
                    errs.append(f"{name}: missing procedures.md")
            else:
                r = subprocess.run([sys.executable, "scripts/route_task.py", "fix typo in readme"], cwd=t / "aos", capture_output=True, text=True)
                if r.returncode != 0:
                    errs.append(f"{name}: route_task smoke failed: {r.stderr[-200:]}")
                r = subprocess.run([sys.executable, "scripts/select_skills.py", "add oauth endpoint"], cwd=t / "aos", capture_output=True, text=True)
                if r.returncode != 0:
                    errs.append(f"{name}: select_skills smoke failed: {r.stderr[-200:]}")
        else:
            h = ".claude" if "claude-code" in name else ".codex"
            skills = t / h / "skills"
            found = list(skills.glob("*/SKILL.md"))
            for sk in found:
                errs += validate_skill(sk)
            if [x.parent.name for x in found] != ["aos"]:
                errs.append(f"{name}: expected exactly one master skill 'aos', found {[x.parent.name for x in found]}")
            if not (skills / "aos" / "modules" / "skills" / "aos-optimize.md").exists():
                errs.append(f"{name}: master skill modules missing")
            rt = t / h / "aos" / "scripts"
            r = subprocess.run([sys.executable, str(rt / "route_task.py"), "implement a new export endpoint"], cwd=t, capture_output=True, text=True)
            if r.returncode != 0:
                errs.append(f"{name}: runtime smoke failed: {r.stderr[-200:]}")
            ag = [x.name for x in (t / h / "agents").glob("*")]
            if ag != ["aos.md" if h == ".claude" else "aos.toml"]:
                errs.append(f"{name}: expected exactly one master agent, found {ag}")
        if prof == "dormakaba":
            root = next((p for p in (t / "aos", t / ".claude" / "aos", t / ".codex" / "aos") if (p / "config").exists()), None)
            if root is None:
                errs.append(f"{name}: cannot find config dir")
            else:
                pol = parse_yaml((root / "config" / "dormakaba-policy.yaml").read_text())
                for rel in pol.get("forbidden_files") or []:
                    if (root / rel).exists():
                        errs.append(f"{name}: forbidden file present {rel}")
                if (root / "scripts" / "policy_guard.py").exists():
                    r = subprocess.run([sys.executable, "scripts/policy_guard.py", "--root", str(root)], cwd=root, capture_output=True, text=True)
                    if r.returncode != 0:
                        errs.append(f"{name}: policy_guard failed: {r.stdout[-300:]}")
        else:
            if any(p.name in ("dormakaba-policy.yaml",) for p in t.rglob("*")):
                errs.append(f"{name}: default build must not ship dormakaba policy")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--out", default="dist")
    v = sub.add_parser("verify"); v.add_argument("dir")
    s = sub.add_parser("stage"); s.add_argument("profile", choices=PROFILES); s.add_argument("out")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        for z in build_all(Path(a.out)):
            print(f"{z.name:46} {z.stat().st_size // 1024:5} KB")
        return 0
    if a.cmd == "stage":
        print(stage(a.profile, Path(a.out)))
        return 0
    errs = [e for z in sorted(Path(a.dir).glob("aos-*.zip")) for e in verify_zip(z)]
    print("dist: OK" if not errs else f"dist: {len(errs)} problem(s)")
    for e in errs:
        print(" -", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
