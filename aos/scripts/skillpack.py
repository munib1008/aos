#!/usr/bin/env python3
"""Build the single master skill `aos` (SKILL.md + modules read on demand) from the source skills/agents/roles.
Used by install.py (project/global installs) and packaging/package.py (distribution zips). Standard library only.

  make_skill_dir(src, profile, noscript, out, prefix=None) -> Path(out/aos)
    prefix=None            self-contained skill (scripts/config/core/templates/docs copied inside)
    prefix='.claude/aos'   bundle mode: runtime installed separately, paths rewritten to that prefix
    noscript=True          code-free variant: script calls become pointers to written procedures (templates/skill/procedures.md)
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".aos")

TCCM_SKILLS = {"aos-token-capacity", "aos-local-inference", "aos-model-routing"}
TCCM_COMMANDS = {"aos-capacity", "aos-model"}
NOSCRIPT_CONFIG = ("models", "optimization", "security", "privacy", "skills", "projects", "dormakaba-policy")
SCRIPT_TO_PROC = {"memory": "P1", "route_task": "P2", "select_skills": "P3", "build_context": "P4", "index_repository": "P9", "summarize_tests": "P5",
                  "summarize_changes": "P5", "session_manager": "P6", "create_handoff": "P7", "policy_guard": "P8", "video_pipeline": "P10",
                  "validate_memory": "P1", "validate_skills": "P11", "measure_usage": "P11", "benchmark_context": "P11", "discover_history": "P9",
                  "export_codex": "P11", "audit_repo": "P9", "tccm": "P2", "omega": "P12", "omega_runner": "P12"}
BLOCK = re.compile(r"<!-- profile:(\w+) -->(.*?)<!-- /profile -->", re.S)


def apply_blocks(text: str, profile: str) -> str:
    text = BLOCK.sub(lambda m: m.group(2) if m.group(1) == profile else "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]+\n", "\n", text)




# ----------------------------------------------------------------------------- single-skill form
def _module_text(name: str, body: str, noscript: bool, prefix: str | None = None) -> str:
    if not noscript:
        if prefix:                                     # bundle mode: runtime lives at <harness>/aos next to the skill
            return body.replace("aos/scripts/", f"{prefix}/scripts/").replace("aos/docs/", f"{prefix}/docs/")
        return body.replace("python3 aos/scripts/", "python3 scripts/").replace("aos/scripts/", "scripts/").replace("aos/docs/", "docs/")

    def sub(m):
        s = re.search(r"([a-z_]+)\.py", m.group(0))
        return f"(procedure {SCRIPT_TO_PROC.get(s.group(1), 'P2')} in procedures.md)" if s else m.group(0)
    body = re.sub(r"`python3 aos/scripts/[^`]*`", sub, body)
    body = re.sub(r"aos/scripts/(\w+)\.py", lambda m: f"procedure {SCRIPT_TO_PROC.get(m.group(1), 'P2')}", body)
    return body.replace("aos/docs/", "docs/").replace("aos/scripts/", "(not bundled; see procedures.md) ")


def make_skill_dir(st: Path, profile: str, noscript: bool, out: Path, prefix: str | None = None) -> Path:
    """Single master skill `aos` (SKILL.md + modules). prefix=None: self-contained (scripts/config inside). prefix='.claude/aos': bundle mode, runtime installed separately."""
    d = out / "aos"
    (d / "modules" / "skills").mkdir(parents=True)
    (d / "modules" / "agents").mkdir()
    (d / "modules" / "roles").mkdir()
    (d / "modules" / "commands").mkdir()
    skills = []
    for sk in sorted((st / "skills").glob("*/SKILL.md")):
        n = sk.parent.name
        if noscript and n in TCCM_SKILLS:
            continue
        text = sk.read_text(encoding="utf-8")
        body = text.split("\n---\n", 1)[1] if text.startswith("---\n") else text
        desc = re.search(r"^description:\s*(.+)$", text, re.M)
        (d / "modules" / "skills" / f"{n}.md").write_text(f"<!-- {n}: {desc.group(1)[:200] if desc else ''} -->\n" + _module_text(n, body.lstrip("\n"), noscript, prefix), encoding="utf-8")
        skills.append(n)
        for extra in sk.parent.rglob("*"):
            if extra.is_file() and extra.name != "SKILL.md":
                t = d / "modules" / "references" / n / extra.relative_to(sk.parent)
                t.parent.mkdir(parents=True, exist_ok=True)
                if (noscript or prefix) and extra.suffix == ".md":
                    t.write_text(_module_text(extra.name, extra.read_text(encoding="utf-8"), noscript, prefix), encoding="utf-8")
                else:
                    t.write_bytes(extra.read_bytes())
    for f in sorted((st / "agents").glob("*.md")):          # the master agent (copyable into an agents folder)
        t = f.read_text(encoding="utf-8")
        (d / "modules" / "agents" / f.name).write_text(_module_text(f.stem, t if noscript is False else t.split("\n---\n", 1)[1].lstrip("\n"), noscript, prefix), encoding="utf-8")
    for f in sorted((st / "roles").glob("*.md")):           # role details the master reads on demand
        t = f.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        (d / "modules" / "roles" / f.name).write_text(_module_text(f.stem, t.lstrip("\n"), noscript, prefix), encoding="utf-8")
    for f in sorted((st / "roles" / "commands").glob("*.md")):
        if noscript and f.stem in TCCM_COMMANDS:
            continue
        (d / "modules" / "commands" / f.name).write_text(_module_text(f.stem, f.read_text(encoding="utf-8").split("\n---\n", 1)[1].lstrip("\n"), noscript, prefix), encoding="utf-8")
    if prefix is None:
        cfg = d / "config"
        cfg.mkdir()
        for f in (st / "config").glob("*"):
            if f.suffix in (".yaml", "") and (not noscript or f.stem in NOSCRIPT_CONFIG or f.name == "PROFILE"):
                shutil.copy(f, cfg / f.name)
        shutil.copytree(st / "templates", d / "templates", ignore=shutil.ignore_patterns("skill"))
        (d / "docs").mkdir()
        for f in ("DESIGN.md", "BENCHMARKS.md", "COMPATIBILITY.md", "DORMAKABA_POLICY.md", "BUILD_VS_REUSE.md", "REUSE_LOG.md"):
            if (st / "docs" / f).exists() and not (noscript and f in ("BUILD_VS_REUSE.md", "REUSE_LOG.md")):
                txt = (st / "docs" / f).read_text(encoding="utf-8")
                (d / "docs" / f).write_text(_module_text(f, txt, True) if noscript else txt, encoding="utf-8")
        if not noscript:
            shutil.copytree(st / "scripts", d / "scripts", ignore=IGNORE)
            if (st / "core").exists():
                shutil.copytree(st / "core", d / "core", ignore=IGNORE)
        else:
            shutil.copy(st / "templates" / "skill" / "procedures.md", d / "procedures.md")
            apply = apply_blocks((d / "procedures.md").read_text(encoding="utf-8"), profile)
            (d / "procedures.md").write_text(apply, encoding="utf-8")
    core = [s for s in skills if s.startswith("aos-") and s in ("aos-optimize", "aos-task-router", "aos-context", "aos-repo-scan", "aos-validation", "aos-learning", "aos-session-fork", "aos-handoff")]
    spec = [s for s in skills if s not in core and s not in TCCM_SKILLS and s not in ("model-advisor", "humanizer")]
    mods = ["- Core: " + ", ".join(f"`modules/skills/{s}.md`" for s in core),
            "- Specialist: " + ", ".join(f"`modules/skills/{s}.md`" for s in spec)]
    comp = [s for s in ("model-advisor", "humanizer") if s in skills]
    if comp:
        mods.append("- Companion: " + ", ".join(f"`modules/skills/{s}.md`" for s in comp))
    tc = [s for s in skills if s in TCCM_SKILLS]
    if tc:
        mods.append("- Capacity and routing (TCCM): " + ", ".join(f"`modules/skills/{s}.md`" for s in tc))
    mods += ["- Master agent: `modules/agents/aos.md` (copy to your agents folder to run `--agent aos`); role details it reads on demand: `modules/roles/` (orchestrator, router, navigator, context-optimizer, session-manager, handoff, learning, testing, minimal-change, omega)", "- Command prompts: `modules/commands/`"]
    tpl = (st / "templates" / "skill" / ("SKILL.noscript.md" if noscript else "SKILL.script.md")).read_text(encoding="utf-8")
    skill = apply_blocks(tpl.replace("{{MODULES}}", "\n".join(mods)).replace("{{TITLE_SUFFIX}}", " (dormakaba build)" if profile == "dormakaba" else ""), profile)
    if prefix:
        skill = (skill.replace("python3 scripts/", f"python3 {prefix}/scripts/").replace("`scripts/", f"`{prefix}/scripts/").replace("`config/", f"`{prefix}/config/")
                 .replace("`docs/", f"`{prefix}/docs/").replace("`templates/", f"`{prefix}/templates/").replace("Run scripts from this folder:", "Run scripts from the project root:"))
    (d / "SKILL.md").write_text(skill, encoding="utf-8")
    notes = st / "THIRD_PARTY_NOTICES.md"
    if notes.exists():
        shutil.copy(notes, d / "THIRD_PARTY_NOTICES.md")
    (d / "README.md").write_text(f"# AOS skill ({profile}{', script-free' if noscript else ''})\n\nInstall: Claude Code `~/.claude/skills/aos/` (or `<project>/.claude/skills/aos/`); Codex `~/.codex/skills/aos/`; "
                                 + ("claude.ai: upload this folder as a skill (Settings > Capabilities > Skills).\n" if noscript else "scripts need Python 3.9+ and run from this folder (`python3 scripts/route_task.py --help`).\n")
                                 + "Entry point: SKILL.md. Honest limits: routing is uncalibrated; savings are unmeasured (docs/BENCHMARKS.md).\n", encoding="utf-8")
    return d


