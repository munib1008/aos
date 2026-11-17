#!/usr/bin/env python3
"""Plan -> render -> verify pipeline for simple automated videos using only local FFmpeg (no Python deps).

  video_pipeline.py init PLAN.json                 # write a starter plan
  video_pipeline.py build PLAN.json -o out.mp4 [--dry-run] [--json]
  video_pipeline.py verify out.mp4 [--expect-duration S --tolerance 0.6 --expect-size 1280x720 --require-audio --max-black 0.4]
  video_pipeline.py selftest [--keep DIR]          # generate synthetic assets, build, verify

Scope: title cards, images, clips, per-shot captions (drawtext), narration/music mix, SRT sidecar, objective checks.
For advanced editing (HDR, sync, multicam, loudness, platform exports) delegate to the external `ffmpeg-skill`
(MIT, pinned commit in docs/REUSE_LOG.md) via env AOS_FFMPEG_SKILL=/path/to/checkout; this file does not copy it.
All subprocess calls use argument lists (no shell). Text is passed to drawtext via files, never interpolated.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]
STARTER = {
    "resolution": "1280x720", "fps": 24, "font": None,
    "shots": [
        {"type": "title", "text": "Product update", "duration": 3, "bg": "0x102040"},
        {"type": "image", "src": "assets/screen1.png", "duration": 4, "caption": "New dashboard"},
        {"type": "video", "src": "assets/demo.mp4", "duration": 5, "caption": "Live demo"},
    ],
    "audio": {"narration": None, "music": None, "music_volume": 0.15},
    "srt": True,
}


class PipelineError(Exception):
    pass


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def sh(cmd: list[str], timeout: int = 900) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise PipelineError(f"{cmd[0]} failed ({p.returncode}): {p.stderr.strip()[-600:]}")
    return p.stdout + p.stderr


def esc_filter_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def parse_size(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", s or "")
    if not m or int(m[1]) % 2 or int(m[2]) % 2:
        raise PipelineError(f"resolution must be WxH with even numbers, got {s!r}")
    return int(m[1]), int(m[2])


def validate_plan(plan: dict, base: Path) -> None:
    parse_size(plan.get("resolution", "1280x720"))
    if not 1 <= int(plan.get("fps", 24)) <= 60:
        raise PipelineError("fps must be 1-60")
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        raise PipelineError("plan needs at least one shot")
    for i, s in enumerate(shots):
        if s.get("type") not in ("title", "image", "video"):
            raise PipelineError(f"shot {i}: type must be title|image|video")
        if not 0.5 <= float(s.get("duration", 0)) <= 600:
            raise PipelineError(f"shot {i}: duration must be 0.5-600 s")
        if s["type"] != "title" and not (base / s.get("src", "")).is_file():
            raise PipelineError(f"shot {i}: source file not found: {s.get('src')}")
        if s["type"] == "title" and not s.get("text"):
            raise PipelineError(f"shot {i}: title needs text")
    for k in ("narration", "music"):
        p = (plan.get("audio") or {}).get(k)
        if p and not (base / p).is_file():
            raise PipelineError(f"audio.{k} not found: {p}")


def find_font(plan: dict) -> str | None:
    for f in [plan.get("font")] + FONTS:
        if f and Path(f).is_file():
            return f
    return None


def drawtext(textfile: Path, font: str, size_h: int, y: str, box: bool) -> str:
    fs = max(18, size_h // 18)
    f = (f"drawtext=fontfile='{esc_filter_path(font)}':textfile='{esc_filter_path(str(textfile))}':fontcolor=white:"
         f"fontsize={fs}:x=(w-text_w)/2:y={y}")
    return f + (":box=1:boxcolor=black@0.55:boxborderw=14" if box else "")


def fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def build(plan: dict, out: Path, base: Path, dry_run: bool = False) -> dict:
    validate_plan(plan, base)
    if not dry_run and not (have("ffmpeg") and have("ffprobe")):
        raise PipelineError("ffmpeg/ffprobe not found on PATH")
    W, H = parse_size(plan.get("resolution", "1280x720"))
    fps = int(plan.get("fps", 24))
    font = find_font(plan)
    warnings = [] if font else ["no font found: captions/titles will not be burned in (SRT sidecar still written)"]
    work = Path(tempfile.mkdtemp(prefix="aos-video-"))
    cmds: list[list[str]] = []
    segs, srt, t0 = [], [], 0.0
    fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    for i, s in enumerate(plan["shots"]):
        d = float(s["duration"])
        seg = work / f"seg{i:03}.mp4"
        text = s.get("text") if s["type"] == "title" else s.get("caption")
        vf = fit
        if text and font:
            tf = work / f"t{i:03}.txt"
            tf.write_text(text, encoding="utf-8")
            vf += "," + drawtext(tf, font, H, "(h-text_h)/2" if s["type"] == "title" else "h-text_h-h/12", s["type"] != "title")
        if text:
            srt.append((t0, t0 + d, text))
        if s["type"] == "title":
            src = ["-f", "lavfi", "-i", f"color=c={s.get('bg', '0x000000')}:s={W}x{H}:r={fps}:d={d}"]
        elif s["type"] == "image":
            src = ["-loop", "1", "-framerate", str(fps), "-t", str(d), "-i", str(base / s["src"])]
        else:
            src = ["-t", str(d), "-i", str(base / s["src"])]
        cmds.append(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *src, "-vf", vf, "-t", str(d), *enc, str(seg)])
        segs.append(seg)
        t0 += d
    lst = work / "list.txt"
    joined = work / "joined.mp4"
    cmds.append(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    a = plan.get("audio") or {}
    nar, mus = (base / a["narration"]) if a.get("narration") else None, (base / a["music"]) if a.get("music") else None
    final = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(joined)]
    if nar or mus:
        for p in (nar, mus):
            if p:
                final += ["-i", str(p)]
        if nar and mus:
            fc = f"[1:a]apad[n];[2:a]volume={float(a.get('music_volume', 0.15))},apad[m];[n][m]amix=inputs=2:duration=longest:normalize=0[a]"
        else:
            fc = "[1:a]apad[a]" if nar else f"[1:a]volume={float(a.get('music_volume', 0.15))},apad[a]"
        final += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{t0:.3f}"]
    else:
        final += ["-c", "copy"]
    final += ["-movflags", "+faststart", str(out)]
    cmds.append(final)
    result = {"output": str(out), "planned_duration": round(t0, 3), "shots": len(segs), "warnings": warnings, "commands": cmds if dry_run else len(cmds), "dry_run": dry_run}
    if dry_run:
        shutil.rmtree(work, ignore_errors=True)
        return result
    try:
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8")
        for c in cmds:
            sh(c)
        if plan.get("srt", True) and srt:
            out.with_suffix(".srt").write_text("".join(f"{n}\n{fmt_srt(a_)} --> {fmt_srt(b_)}\n{t}\n\n" for n, (a_, b_, t) in enumerate(srt, 1)), encoding="utf-8")
            result["srt"] = str(out.with_suffix(".srt"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


def probe(path: Path) -> dict:
    return json.loads(sh(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))


def verify(path: Path, expect_duration: float | None = None, tolerance: float = 0.6, expect_size: str | None = None,
           require_audio: bool = False, max_black: float | None = None) -> dict:
    checks, fails = {}, []
    if not path.is_file() or path.stat().st_size == 0:
        return {"status": "FAILED", "failures": ["output missing or empty"], "checks": {}}
    try:
        j = probe(path)
    except PipelineError as e:
        return {"status": "FAILED", "failures": [f"ffprobe cannot read file: {e}"], "checks": {}}
    v = [s for s in j["streams"] if s["codec_type"] == "video"]
    au = [s for s in j["streams"] if s["codec_type"] == "audio"]
    dur = float(j["format"].get("duration", 0))
    checks.update(duration=round(dur, 3), video_streams=len(v), audio_streams=len(au), size_bytes=int(j["format"].get("size", 0)))
    if not v:
        fails.append("no video stream")
    else:
        checks["resolution"] = f"{v[0]['width']}x{v[0]['height']}"
        checks["codec"] = v[0]["codec_name"]
        if expect_size and checks["resolution"] != expect_size:
            fails.append(f"resolution {checks['resolution']} != {expect_size}")
    if dur <= 0:
        fails.append("zero duration")
    if expect_duration is not None and abs(dur - expect_duration) > tolerance:
        fails.append(f"duration {dur:.2f}s differs from expected {expect_duration:.2f}s by more than {tolerance}s")
    if require_audio and not au:
        fails.append("audio required but no audio stream")
    if max_black is not None and v:
        out = sh(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-vf", "blackdetect=d=0.2:pix_th=0.10", "-an", "-f", "null", "-"])
        black = sum(float(m) for m in re.findall(r"black_duration:([\d.]+)", out))
        checks["black_ratio"] = round(black / dur, 3) if dur else None
        if dur and black / dur > max_black:
            fails.append(f"black frames {black / dur:.0%} of duration exceeds {max_black:.0%}")
    return {"status": "FAILED" if fails else "PASSED", "failures": fails, "checks": checks}


def selftest(keep: Path | None = None) -> dict:
    d = keep or Path(tempfile.mkdtemp(prefix="aos-vtest-"))
    d.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=1", "-frames:v", "1", str(d / "a.png")])
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=3", "-pix_fmt", "yuv420p", str(d / "c.mp4")])
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:d=9", str(d / "n.wav")])
    plan = {"resolution": "640x360", "fps": 24, "shots": [{"type": "title", "text": "AOS demo", "duration": 2, "bg": "0x203050"},
            {"type": "image", "src": "a.png", "duration": 2, "caption": "An image shot"}, {"type": "video", "src": "c.mp4", "duration": 3, "caption": "A clip"}],
            "audio": {"narration": "n.wav"}}
    res = build(plan, d / "out.mp4", d)
    return {"build": res, "verify": verify(d / "out.mp4", 7.0, 0.6, "640x360", True, 0.5), "dir": str(d)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init"); i.add_argument("plan")
    b = sub.add_parser("build"); b.add_argument("plan"); b.add_argument("-o", "--out", required=True); b.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify"); v.add_argument("file"); v.add_argument("--expect-duration", type=float); v.add_argument("--tolerance", type=float, default=0.6)
    v.add_argument("--expect-size"); v.add_argument("--require-audio", action="store_true"); v.add_argument("--max-black", type=float)
    s = sub.add_parser("selftest"); s.add_argument("--keep")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            Path(a.plan).write_text(json.dumps(STARTER, indent=2), encoding="utf-8"); print("wrote", a.plan); return 0
        if a.cmd == "build":
            p = Path(a.plan)
            res = build(json.loads(p.read_text(encoding="utf-8")), Path(a.out), p.resolve().parent, a.dry_run)
            print(json.dumps(res, indent=2)); return 0
        if a.cmd == "verify":
            r = verify(Path(a.file), a.expect_duration, a.tolerance, a.expect_size, a.require_audio, a.max_black)
            print(json.dumps(r, indent=2)); return 0 if r["status"] == "PASSED" else 1
        r = selftest(Path(a.keep) if a.keep else None)
        print(json.dumps(r, indent=2)); return 0 if r["verify"]["status"] == "PASSED" else 1
    except (PipelineError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as e:
        print(f"video_pipeline error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
