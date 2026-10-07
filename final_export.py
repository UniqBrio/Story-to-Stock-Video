#!/usr/bin/env python3
"""
final_export.py  (SVOS v2 render layer)
───────────────
Produces the platform-ready MP4 and its delivery package:

    H.264 High@4.2 · yuv420p · BT.709 tagged · closed 2s GOP · CRF (default 18)
    with a 14 Mbps cap · AAC 192k 48 kHz stereo · faststart
    + <name>_cover.jpg   (cover frame — first T1 card or `cover_time`)
    + render_manifest.json (specs, measured loudness, timeline, overlay manifest)
    + project.snapshot.json (audit copy of the project state that produced the file)

Usage:
    python final_export.py project.json
    python final_export.py project.json --force
"""

import re
import sys
import json
import time
import shutil
import argparse
from pathlib import Path
from datetime import datetime

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, run_ff_capture,
                         probe_video_info, banner, set_log, output_dir, variant_suffix)


def measure_final_loudness(path: Path) -> dict:
    rc, err = run_ff_capture(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                              "-af", "ebur128=peak=true", "-f", "null", "-"], "ebur128")
    out = {}
    m = re.search(r"Integrated loudness:\s*I:\s*(-?[0-9.]+) LUFS", err)
    if m:
        out["integrated_lufs"] = float(m.group(1))
    m = re.search(r"Loudness range:\s*LRA:\s*(-?[0-9.]+) LU", err)
    if m:
        out["lra"] = float(m.group(1))
    m = re.search(r"True peak:\s*Peak:\s*(-?[0-9.]+) dBFS", err)
    if m:
        out["true_peak_dbtp"] = float(m.group(1))
    return out


def pick_cover_time(project: dict) -> float:
    ct = str(project.get("render", {}).get("cover_time", "")).strip()
    if ct:
        try:
            return float(ct)
        except ValueError:
            pass
    for t in project.get("timeline", []):
        if t.get("kind") == "card":
            return round(t["start"] + min(1.2, t["dur"] * 0.6), 2)   # card text fully risen
    return 1.0


def export_final(src: Path, dest: Path, tw: int, th: int, fps: int, crf: str, preset: str) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={tw}:{th}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-maxrate", "14M", "-bufsize", "28M",
           "-profile:v", "high", "-level:v", "4.2",
           "-g", str(fps * 2), "-keyint_min", str(fps), "-sc_threshold", "0",
           "-pix_fmt", "yuv420p", "-r", str(fps),
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart",
           "-metadata", f"title={dest.stem}", "-metadata", "comment=UniqBrio SVOS v2",
           str(dest)]
    return run_ff(cmd, "final_export")


def main():
    ap = argparse.ArgumentParser(description="Final Instagram/Facebook export + delivery package")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    check_ffmpeg()
    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    src = project.get("audio_mixed_file", "")
    if not src or not Path(src).exists():
        sys.exit("❌  audio_mixed_file not found — run audio_mixer.py first")

    out_folder = output_dir(project, proj_path)
    out_name = project.get("output_file", "final_video.mp4")
    out_path = out_folder / out_name
    tw, th, fps = project.get("width", 1080), project.get("height", 1920), project.get("fps", 30)
    render = project.get("render", {})
    crf, preset = render.get("crf", "18"), render.get("preset", "slow")

    banner("Final Export", f"{tw}×{th} @ {fps}fps · CRF {crf} · preset {preset}", f"→ {out_path}")

    if out_path.exists() and not args.force:
        print("  ⏭️  Output already exists — use --force to re-export")
        project["final_output"] = str(out_path)
        save_project(project, proj_path)
        return

    t0 = time.time()
    print("  ▶  Rendering final MP4 (2–10 min depending on length and preset)...")
    if not export_final(Path(src), out_path, tw, th, fps, crf, preset):
        sys.exit("  ❌  Final export failed — see Output/logs/pipeline_ffmpeg.log")

    info = probe_video_info(out_path)
    loud = measure_final_loudness(out_path)
    size_mb = out_path.stat().st_size / (1024 * 1024)

    # Cover frame
    cover_t = min(pick_cover_time(project), max(0.0, info["duration"] - 0.1))
    cover = out_folder / f"{out_path.stem}_cover.jpg"
    run_ff(["ffmpeg", "-y", "-ss", f"{cover_t:.2f}", "-i", str(out_path), "-frames:v", "1", "-q:v", "2", str(cover)],
           "cover frame", quiet=True)

    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "project_name": project.get("project_name"),
        "file": str(out_path), "size_mb": round(size_mb, 2),
        "duration_s": round(info["duration"], 3), "width": info["width"], "height": info["height"],
        "fps": round(info["fps"], 3), "video_codec": info["codec"], "profile": info["profile"],
        "pix_fmt": info["pix_fmt"], "audio_codec": info["audio_codec"], "sample_rate": info["sample_rate"],
        "loudness": loud, "target_lufs": project.get("audio", {}).get("target_lufs"),
        "cover_frame": str(cover) if cover.exists() else "", "cover_time": cover_t,
        "timeline": project.get("timeline", []), "overlays": project.get("overlay_manifest", []),
        "audio_report": project.get("audio_report", {}),
        "licenses": [{"shot_id": s["shot_id"], "source": s.get("source", ""), "license": s.get("license", ""),
                      "page_url": s.get("page_url", ""), "author": s.get("author", "")}
                     for s in project["shots"] if s.get("source")],
    }
    mf_name = f"render_manifest{variant_suffix(project)}.json"
    (out_folder / mf_name).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(proj_path, out_folder / "project.snapshot.json")

    project["final_output"] = str(out_path)
    project["render_manifest"] = str(out_folder / mf_name)
    project["cover_frame"] = str(cover) if cover.exists() else ""
    save_project(project, proj_path)

    mins, sec = divmod(int(time.time() - t0), 60)
    print(f"{'█'*62}\n  ✅  DONE  ({mins}m {sec}s)")
    print(f"  Output   : {out_path}")
    print(f"  Duration : {info['duration']:.2f}s   Size: {size_mb:.1f} MB")
    print(f"  Spec     : {info['codec']} {info['profile']} · {info['pix_fmt']} · {info['width']}×{info['height']} · "
          f"{info['fps']:.0f}fps · {info['audio_codec']} {info['sample_rate']} Hz")
    if loud:
        print(f"  Loudness : {loud.get('integrated_lufs', '?')} LUFS · TP {loud.get('true_peak_dbtp', '?')} dBTP "
              f"(target {project.get('audio', {}).get('target_lufs')} / {project.get('audio', {}).get('true_peak')})")
    if cover.exists():
        print(f"  Cover    : {cover.name}  (t={cover_t:.2f}s)")
    max_d = float(render.get("max_duration", 90))
    if info["duration"] > max_d:
        print(f"  ⚠️  {info['duration']:.1f}s exceeds the {max_d:.0f}s target for Reels")
    print(f"{'█'*62}\n")
    print("  NEXT: python qa_check.py project.json   → QA report for G6 (phone, sound OFF first)\n")


if __name__ == "__main__":
    main()
