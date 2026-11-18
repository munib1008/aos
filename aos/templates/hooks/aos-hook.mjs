#!/usr/bin/env node
// Cross-platform launcher for AOS Claude Code hooks (Windows, macOS, Linux). Usage in settings.json:
//   node .claude/hooks/aos-hook.mjs dual    -> dualagent.py hook (blocks Edit/Write on the other agent's paths)
//   node .claude/hooks/aos-hook.mjs omega   -> omega.py hook (queues/denies risky Bash commands)
// Finds Python (py -3, python, python3) and the AOS runtime (~/.claude/aos or ./.claude/aos), forwards stdin and the exit code
// (2 = block). If Python or AOS is missing it FAILS OPEN (exit 0) with a warning on stderr: the guard is advisory, CI checks are the gate.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const kind = process.argv[2];
const script = { dual: "dualagent.py", omega: "omega.py" }[kind];
if (!script) { console.error("aos-hook: usage: aos-hook.mjs dual|omega"); process.exit(0); }

const roots = [join(process.cwd(), ".claude", "aos"), join(homedir(), ".claude", "aos"), join(process.cwd(), "aos")];
const root = roots.find((r) => existsSync(join(r, "scripts", script)));
if (!root) { console.error(`aos-hook: AOS runtime not found (looked in ${roots.join(", ")}); guard skipped`); process.exit(0); }

let input = "";
try { input = (await new Promise((res) => { let d = ""; process.stdin.on("data", (c) => (d += c)); process.stdin.on("end", () => res(d)); process.stdin.on("error", () => res(d)); })); } catch { /* no stdin */ }

const candidates = [["py", ["-3"]], ["python", []], ["python3", []]];
for (const [cmd, pre] of candidates) {
  const r = spawnSync(cmd, [...pre, join(root, "scripts", script), "hook"], { input, encoding: "utf8", cwd: process.cwd() });
  if (r.error) continue;                       // interpreter not found: try the next one
  if (r.stderr) process.stderr.write(r.stderr);
  if (r.stdout) process.stdout.write(r.stdout);
  process.exit(r.status === 2 ? 2 : 0);        // only an explicit block (2) blocks; any other failure fails open
}
console.error("aos-hook: no Python interpreter found (py, python, python3); guard skipped");
process.exit(0);
