#!/usr/bin/env python3
"""Install AOS into a project: agents/skills/commands -> <target>/.claude/, runtime -> <target>/.claude/aos/.

  python3 aos/install.py --target /path/to/project [--dry-run] [--uninstall]

Idempotent. Adds a marked AOS block to the target AGENTS.md/CLAUDE.md (never overwrites existing content) and
`.aos/` to .gitignore. When target == the source repo, the runtime is used in place (aos/scripts).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEGIN, END = "<!-- AOS:BEGIN -->", "<!-- AOS:END -->"


def rewrite(text: str, in_place: bool) -> str:
    return text if in_place else text.replace("aos/scripts/", ".claude/aos/scripts/").replace("aos/docs/", ".claude/aos/docs/")


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True)
    ap.add_argument("--harness", choices=["claude", "codex", "both"], default="claude")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args(argv)
    target = Path(a.target).resolve()
    in_place = target == HERE.parent
    cdir = target / ".claude"
    names = {"agents": sorted((HERE / "agents").glob("*.md")), "commands": sorted((HERE / "commands").glob("*.md")),
             "skills": sorted((HERE / "skills").glob("*/SKILL.md"))}
    if a.uninstall:
        for kind, files in names.items():
            for f in files:
                p = cdir / kind / (f.parent.name if kind == "skills" else f.name)
                if not a.dry_run:
                    shutil.rmtree(p, ignore_errors=True) if kind == "skills" else p.unlink(missing_ok=True)
        if not a.dry_run:
            shutil.rmtree(target / ".claude" / "aos", ignore_errors=True)
        for n in ("AGENTS.md", "CLAUDE.md"):
            block(target / n, "", a.dry_run, True)
        print("AOS uninstalled (state in .aos/ kept)")
        return 0
    claude = a.harness in ("claude", "both")
    for f in (names["agents"] + names["commands"]) if claude else []:
        put(cdir / f.parent.name / f.name, rewrite(f.read_text(encoding="utf-8"), in_place), a.dry_run)
    for f in names["skills"] if claude else []:
        put(cdir / "skills" / f.parent.name / "SKILL.md", rewrite(f.read_text(encoding="utf-8"), in_place), a.dry_run)
    if claude and not in_place and not a.dry_run:
        rt = target / ".claude" / "aos"
        for sub in ("scripts", "config", "templates", "docs"):
            shutil.copytree(HERE / sub, rt / sub, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if a.harness in ("codex", "both") and not a.dry_run:
        sys.path.insert(0, str(HERE / "scripts"))
        import export_codex
        r = export_codex.export(target, in_place)
        if not in_place:
            shutil.copytree(HERE / "scripts", target / ".codex" / "aos" / "scripts", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(HERE / "config", target / ".codex" / "aos" / "config", dirs_exist_ok=True)
        print(f"codex: {len(r['skills'])} skills, {len(r['agents'])} agents -> {target / '.codex'}")
    agents_body = rewrite((HERE / "AGENTS.md").read_text(encoding="utf-8"), in_place)
    claude_body = "@AGENTS.md\n\nAOS slash commands: /aos-optimize /aos-scan /aos-route /aos-learn /aos-fork /aos-resume /aos-handoff /aos-audit"
    block(target / "AGENTS.md", agents_body, a.dry_run, False)
    block(target / "CLAUDE.md", claude_body, a.dry_run, False)
    gi = target / ".gitignore"
    cur = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".aos/" not in cur.split() and not a.dry_run:
        gi.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + ".aos/\n", encoding="utf-8")
    print(f"AOS installed into {cdir} ({'in place' if in_place else 'runtime copied to .claude/aos'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
