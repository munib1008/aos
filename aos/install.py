#!/usr/bin/env python3
"""Install AOS into a project (or globally) for Claude Code and/or Codex.

  python3 aos/install.py --target /path/to/project [--harness claude|codex|both] [--profile default|dormakaba]
  python3 aos/install.py --global [--harness ...] [--profile ...]      # ~/.claude and/or ~/.codex, absolute-style paths
  python3 aos/install.py --target P --uninstall | --dry-run

Claude Code : <target>/.claude/agents/aos.md + .claude/skills/aos/ + .claude/commands/aos.md + runtime in .claude/aos
Codex       : <target>/.codex/agents/aos.toml + .codex/skills/aos/ + runtime in .codex/aos
--profile builds the profile distribution first (config overlay baked in, forbidden features removed) and installs that.
Idempotent; appends a marked AOS block to AGENTS.md/CLAUDE.md (never overwrites), adds `.aos/` to .gitignore (project installs).
When target == the AOS source repo itself, the runtime is used in place (aos/scripts).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEGIN, END = "<!-- AOS:BEGIN -->", "<!-- AOS:END -->"
RUNTIME = ("scripts", "config", "templates", "docs", "core")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def rewrite(text: str, prefix: str | None) -> str:
    """prefix None => keep source paths (aos/scripts); else e.g. '.claude/aos' or '~/.claude/aos'."""
    return text if prefix is None else text.replace("aos/scripts/", f"{prefix}/scripts/").replace("aos/docs/", f"{prefix}/docs/")


def put(dst: Path, text: str, dry: bool) -> None:
    if dry:
        print("would write", dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")


def block(path: Path, body: str, dry: bool, uninstall: bool) -> None:
    cur = path.read_text(encoding="utf-8") if path.exists() else ""
    if BEGIN in cur:
        cur = cur[:cur.index(BEGIN)].rstrip() + ("\n" + cur[cur.index(END) + len(END):].lstrip() if END in cur else "")
        cur = cur.rstrip() + "\n" if cur.strip() else ""
    if not uninstall:
        cur = (cur.rstrip() + "\n\n" if cur.strip() else "") + f"{BEGIN}\n{body.strip()}\n{END}\n"
    if dry:
        print("would update", path)
    elif cur.strip():
        path.write_text(cur, encoding="utf-8")
    elif path.exists():
        path.unlink()


def install(src: Path, target: Path, harness: str, in_place: bool, glob: bool, dry: bool, uninstall: bool) -> None:
    """One master agent (`aos`), ONE master skill (`aos`, modules read on demand), one /aos command, runtime scripts."""
    legacy_agents = [f.stem for f in (src / "roles").glob("*.md")] if (src / "roles").exists() else []
    legacy_skills = [f.parent.name for f in (src / "skills").glob("*/SKILL.md")]
    legacy_cmds = [f.stem for f in (src / "roles" / "commands").glob("*.md")] + ["omega"] if (src / "roles").exists() else ["omega"]
    for hn in ("claude", "codex"):
        if harness not in (hn, "both"):
            continue
        base = target / f".{hn}"
        prefix = "aos" if in_place else (f"~/.{hn}/aos" if glob else f".{hn}/aos")
        if uninstall:
            if not dry:
                for p_ in (base / "skills" / "aos", base / "aos"):
                    shutil.rmtree(p_, ignore_errors=True)
                (base / "agents" / ("aos.md" if hn == "claude" else "aos.toml")).unlink(missing_ok=True)
                (base / "commands" / "aos.md").unlink(missing_ok=True)
            continue
        if dry:
            print("would install master agent/skill into", base)
            continue
        sys.path.insert(0, str(src / "scripts"))
        import skillpack as pack
        profile = (src / "config" / "PROFILE").read_text().strip() if (src / "config" / "PROFILE").exists() else "default"
        tmp = Path(tempfile.mkdtemp(prefix="aos-skill-"))
        skill_dir = pack.make_skill_dir(src, profile, False, tmp, prefix)
        shutil.rmtree(base / "skills" / "aos", ignore_errors=True)
        shutil.copytree(skill_dir, base / "skills" / "aos")
        for n in legacy_skills:                                   # migrate older granular installs
            if n != "aos":
                shutil.rmtree(base / "skills" / n, ignore_errors=True)
        for n in legacy_agents:
            (base / "agents" / f"{n}.md").unlink(missing_ok=True)
            (base / "agents" / f"{n}.toml").unlink(missing_ok=True)
        if hn == "claude":
            for f in (src / "agents").glob("*.md"):
                put(base / "agents" / f.name, rewrite(pack.apply_blocks(f.read_text(encoding="utf-8"), profile), prefix), dry)
            for f in (src / "commands").glob("*.md"):
                put(base / "commands" / f.name, rewrite(pack.apply_blocks(f.read_text(encoding="utf-8"), profile), prefix), dry)
            for n in legacy_cmds:
                (base / "commands" / f"{n}.md").unlink(missing_ok=True)
        else:
            sys.path.insert(0, str(src / "scripts"))
            import export_codex
            export_codex.export(target, in_place, src=src, prefix=prefix, skills=False)
        if not in_place:
            for sub in RUNTIME:
                if (src / sub).exists():
                    shutil.copytree(src / sub, base / "aos" / sub, dirs_exist_ok=True, ignore=IGNORE)
    if not glob:
        if uninstall:
            for n in ("AGENTS.md", "CLAUDE.md"):
                block(target / n, "", dry, True)
        else:
            body = rewrite((src / "AGENTS.md").read_text(encoding="utf-8"), None if in_place else (".codex/aos" if harness == "codex" else ".claude/aos"))
            block(target / "AGENTS.md", body, dry, False)
            if harness in ("claude", "both"):
                block(target / "CLAUDE.md", "@AGENTS.md\n\nAOS: one master agent and one skill. Run `claude --agent aos \"<task or goal>\"` or /aos <task>.", dry, False)
            gi = target / ".gitignore"
            cur = gi.read_text(encoding="utf-8") if gi.exists() else ""
            if ".aos/" not in cur.split() and not dry:
                gi.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + ".aos/\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target"); ap.add_argument("--global", dest="glob", action="store_true")
    ap.add_argument("--harness", choices=["claude", "codex", "both"], default="claude")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args(argv)
    if not a.target and not a.glob:
        ap.error("give --target PROJECT or --global")
    target = Path.home() if a.glob else Path(a.target).resolve()
    src, in_place, tmp = HERE, (not a.glob and target == HERE.parent and a.profile == "default"), None
    if a.profile != "default":
        sys.path.insert(0, str(HERE / "packaging"))
        import package
        tmp = Path(tempfile.mkdtemp(prefix="aos-profile-"))
        src = package.stage(a.profile, tmp)
    install(src, target, a.harness, in_place, a.glob, a.dry_run, a.uninstall)
    print(f"AOS {'uninstalled' if a.uninstall else 'installed'} -> {target} [{a.harness}, profile {a.profile}{', in place' if in_place else ''}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
