#!/usr/bin/env python3
"""Generate Codex-compatible artifacts from the single Claude-format source (skills/, agents/, commands/).

  export_codex.py --target PROJECT [--in-place-paths]
Emits (modelled on the pattern in wshobson/agents tools/adapters/codex.py; code written independently):
  <target>/.codex/skills/<name>/SKILL.md     skills (+commands without a same-named skill, synthesized as skills)
  <target>/.codex/agents/<name>.toml         agents -> name, description, [model], sandbox_mode, developer_instructions
AGENTS.md is shared (AOS block appended by install.py). Claude-only frontmatter (tools, model, allowed-tools, ...) is stripped.
Codex path/limit assumptions (.codex/skills, 8 KB skill cap, 32 KiB AGENTS.md, TOML agent keys) come from a third-party adapter
and openai/plugins manifests, NOT from running Codex here: validate in your Codex version (docs/COMPATIBILITY.md).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from aos_lib import AOS_DIR, load_config
from validate_skills import CODEX_CAP, split_frontmatter, validate_skill

KEEP = ("name", "description", "license", "compatibility")


def tr(text: str, in_place: bool) -> str:
    text = text.replace("$ARGUMENTS", "the user's request").replace("/aos-", "the aos-")
    return text if in_place else text.replace("aos/scripts/", ".codex/aos/scripts/").replace("aos/docs/", ".codex/aos/docs/")


def skill_md(fm: dict, body: str) -> str:
    head = "\n".join(f"{k}: {fm[k]}" for k in KEEP if fm.get(k))
    return f"---\n{head}\n---\n{body.lstrip(chr(10))}"


def toml_str(v: str) -> str:
    if "\n" in v:
        return '"""\n' + v.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '\n"""'
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def export(target: Path, in_place: bool = False) -> dict:
    out_skills = target / ".codex" / "skills"
    out_agents = target / ".codex" / "agents"
    models = load_config("models").get("codex_model_map") or {}
    made = {"skills": [], "agents": []}
    names = set()
    for sk in sorted((AOS_DIR / "skills").glob("*/SKILL.md")):
        fm, body = split_frontmatter(sk.read_text(encoding="utf-8"))
        d = out_skills / fm["name"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(tr(skill_md(fm, body), in_place), encoding="utf-8")
        names.add(fm["name"])
        made["skills"].append(fm["name"])
    for cm in sorted((AOS_DIR / "commands").glob("*.md")):       # Codex deprecated custom prompts in favour of skills (per adapter notes)
        if cm.stem in names:
            continue
        fm, body = split_frontmatter(cm.read_text(encoding="utf-8"))
        d = out_skills / cm.stem
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(tr(skill_md({"name": cm.stem, "description": fm["description"] + ". Use when the user asks for it by name."}, body), in_place), encoding="utf-8")
        made["skills"].append(cm.stem)
    out_agents.mkdir(parents=True, exist_ok=True)
    for ag in sorted((AOS_DIR / "agents").glob("*.md")):
        fm, body = split_frontmatter(ag.read_text(encoding="utf-8"))
        tools = {t.strip() for t in fm.get("tools", "").split(",")}
        sandbox = "read-only" if tools <= {"Read", "Grep", "Glob"} else "workspace-write"   # Bash can write logs/state
        lines = [f"name = {toml_str(fm['name'])}", f"description = {toml_str(fm['description'])}"]
        if fm.get("model") in models:
            lines.append(f"model = {toml_str(models[fm['model']])}")
        lines += [f"sandbox_mode = {toml_str(sandbox)}", f"developer_instructions = {toml_str(tr(body.strip(), in_place))}"]
        (out_agents / f"{fm['name']}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        made["agents"].append(fm["name"])
    errs = [e for s in out_skills.glob("*/SKILL.md") if s.parent.name in made["skills"] for e in validate_skill(s)]
    made["errors"] = errs
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True)
    ap.add_argument("--in-place-paths", action="store_true", help="keep aos/scripts paths (target is the AOS source repo)")
    a = ap.parse_args(argv)
    r = export(Path(a.target).resolve(), a.in_place_paths)
    print(f"codex export: {len(r['skills'])} skills, {len(r['agents'])} agents -> {a.target}/.codex")
    for e in r["errors"]:
        print(" -", e)
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
