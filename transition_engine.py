#!/usr/bin/env python3
"""
transition_engine.py  (SVOS v2 render layer)
────────────────────
Assembles all normalised clips into ONE video in a single FFmpeg pass:
cut-joined runs are concatenated in-graph, and every dissolve / fade_black /
fade_white boundary is an xfade — no pairwise re-encoding, no generation loss.

It also writes the AUTHORITATIVE TIMELINE to project.json (shot start/end in
the assembled file, after transition overlaps). overlay_engine, audio_mixer,
bgm_prompt_generator and qa_check all read that timeline — which fixes the v1
drift where overlays were timed from planned durations and slid later after
every dissolve.

Output: Assets/Assembled/assembled_raw.mp4  (video-only)

Usage:
    python transition_engine.py project.json
    python transition_engine.py project.json --force
"""

import sys
import shutil
import argparse
import tempfile
from pathlib import Path

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, probe_video_info,
                         compute_timeline, timeline_total, REAL_TRANSITIONS, banner, set_log,
                         assets_dir, output_dir)

XFADE_TYPE = {"dissolve": "dissolve", "fade_black": "fade", "fade_white": "fadewhite"}


def encode_args(fps: int) -> list[str]:
    return ["-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-an",
            "-movflags", "+faststart"]


def collect_clips(project: dict) -> list[dict]:
    norm_map = project.get("normalised_map", {})
    clips = []
    for shot in project["shots"]:
        sid = shot["shot_id"]
        norm = norm_map.get(sid) or shot.get("normalised_file", "")
        if not norm or not Path(norm).exists():
            print(f"  ⚠️  {sid} — normalised file missing, shot DROPPED from assembly")
            continue
        info = probe_video_info(norm)
        clips.append({"shot": shot, "path": Path(norm), "dur": info["duration"] or float(shot.get("duration", 4.0)),
                      "transition": shot.get("transition_out", "cut"), "trans_dur": float(shot.get("trans_dur", 0.0))})
    # Clamp transitions so an xfade never exceeds either neighbour
    for i in range(len(clips) - 1):
        c, n = clips[i], clips[i + 1]
        if c["transition"] in REAL_TRANSITIONS and c["trans_dur"] > 0:
            limit = max(0.0, min(c["dur"], n["dur"]) - 0.25)
            if c["trans_dur"] > limit:
                print(f"  ⚠️  {c['shot']['shot_id']} — trans_dur {c['trans_dur']}s clamped to {limit:.2f}s")
                c["trans_dur"] = round(limit, 3)
                c["shot"]["trans_dur"] = c["trans_dur"]
            if c["trans_dur"] <= 0:
                c["transition"] = "cut"
        else:
            c["trans_dur"] = 0.0
    if clips:
        clips[-1]["trans_dur"] = 0.0
    return clips


def build_single_pass(clips: list[dict], out_file: Path, tw: int, th: int, fps: int) -> bool:
    """One filter graph: concat cut-groups, then chain xfades between groups."""
    n = len(clips)
    inputs = []
    for c in clips:
        inputs += ["-i", str(c["path"])]
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]scale={tw}:{th},setsar=1,fps={fps},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[c{i}]")

    # Group consecutive cut-joined clips
    groups: list[list[int]] = [[0]]
    boundaries: list[tuple[str, float]] = []
    for i in range(n - 1):
        c = clips[i]
        if c["transition"] in REAL_TRANSITIONS and c["trans_dur"] > 0:
            boundaries.append((c["transition"], c["trans_dur"]))
            groups.append([i + 1])
        else:
            groups[-1].append(i + 1)

    seg_labels, seg_durs = [], []
    for gi, g in enumerate(groups):
        if len(g) == 1:
            seg_labels.append(f"[c{g[0]}]")
        else:
            parts.append("".join(f"[c{i}]" for i in g) + f"concat=n={len(g)}:v=1:a=0[g{gi}]")
            seg_labels.append(f"[g{gi}]")
        seg_durs.append(sum(clips[i]["dur"] for i in g))

    if len(seg_labels) == 1:
        parts.append(f"{seg_labels[0]}copy[vout]")
    else:
        cur = seg_labels[0]
        cur_dur = seg_durs[0]
        for j in range(1, len(seg_labels)):
            trans, tdur = boundaries[j - 1]
            offset = max(0.0, cur_dur - tdur)
            lab = "[vout]" if j == len(seg_labels) - 1 else f"[x{j}]"
            parts.append(f"{cur}{seg_labels[j]}xfade=transition={XFADE_TYPE.get(trans, 'dissolve')}"
                         f":duration={tdur:.3f}:offset={offset:.3f}{lab}")
            cur = lab
            cur_dur = cur_dur + seg_durs[j] - tdur

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", "[vout]",
           *encode_args(fps), str(out_file)]
    return run_ff(cmd, "assemble (single pass)")


def build_iterative(clips: list[dict], out_file: Path, tw: int, th: int, fps: int) -> bool:
    """Fallback: fold clips pairwise (slower, but isolates a failing transition)."""
    with tempfile.TemporaryDirectory(prefix="svos_asm_") as tmp:
        tmp = Path(tmp)
        cur = clips[0]["path"]
        cur_dur = clips[0]["dur"]
        for j in range(1, len(clips)):
            prev, nxt = clips[j - 1], clips[j]
            out = tmp / f"m_{j:03d}.mp4"
            if prev["transition"] in REAL_TRANSITIONS and prev["trans_dur"] > 0:
                tdur = prev["trans_dur"]
                fc = (f"[0:v]scale={tw}:{th},setsar=1,fps={fps},format=yuv420p,settb=AVTB[a];"
                      f"[1:v]scale={tw}:{th},setsar=1,fps={fps},format=yuv420p,settb=AVTB[b];"
                      f"[a][b]xfade=transition={XFADE_TYPE.get(prev['transition'], 'dissolve')}"
                      f":duration={tdur:.3f}:offset={max(0.0, cur_dur - tdur):.3f}[v]")
                cur_dur = cur_dur + nxt["dur"] - tdur
            else:
                fc = (f"[0:v]scale={tw}:{th},setsar=1,fps={fps},format=yuv420p,settb=AVTB[a];"
                      f"[1:v]scale={tw}:{th},setsar=1,fps={fps},format=yuv420p,settb=AVTB[b];"
                      f"[a][b]concat=n=2:v=1:a=0[v]")
                cur_dur = cur_dur + nxt["dur"]
            cmd = ["ffmpeg", "-y", "-i", str(cur), "-i", str(nxt["path"]), "-filter_complex", fc,
                   "-map", "[v]", *encode_args(fps), str(out)]
            if not run_ff(cmd, f"assemble step {j}"):
                return False
            cur = out
        shutil.copy2(cur, out_file)
    return True


def main():
    ap = argparse.ArgumentParser(description="Assemble all clips with transitions (single pass)")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-drop", action="store_true",
                    help="Assemble even if some shots have no normalised clip (video gets shorter)")
    args = ap.parse_args()

    check_ffmpeg()
    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    tw, th, fps = project.get("width", 1080), project.get("height", 1920), project.get("fps", 30)
    asm_dir = assets_dir(project, proj_path) / "Assembled"
    asm_dir.mkdir(parents=True, exist_ok=True)
    out_file = asm_dir / "assembled_raw.mp4"

    banner("Transition Engine", f"{len(project['shots'])} shots → {out_file}")

    # U-rated gate: never assemble a clip whose source is no longer cleared (e.g. rejected after it was normalised)
    import content_safety as safety
    from svos_common import shot_kind
    blocked = []
    for s in project["shots"]:
        lf = s.get("local_file", "")
        if shot_kind(s) in ("stock", "product") and lf and Path(lf).exists():
            why = safety.require_media(project, lf, bool(s.get("illustration_only")), safety.media_window(s))
            if why:
                blocked.append(f"{s['shot_id']}: {why}")
    if blocked:
        sys.exit("  🛡   Not assembled — content not cleared:\n" + "\n".join(f"     • {b}" for b in blocked))

    clips = collect_clips(project)
    if not clips:
        sys.exit("  ❌  No clips to assemble — run clip_normaliser.py first")
    present = {c["shot"]["shot_id"] for c in clips}
    dropped_ids = [s["shot_id"] for s in project["shots"] if s["shot_id"] not in present]
    dropped = len(dropped_ids)
    if dropped and not args.allow_drop:
        sys.exit(f"\n  ❌  {dropped} shot(s) have no normalised clip: {', '.join(dropped_ids)}\n"
                 f"     Refusing to assemble a video shorter than the plan (the VO would run past the picture).\n"
                 f"     Fix the sources and rerun from Phase 3, or pass --allow-drop.")
    project["dropped_shots"] = dropped_ids

    # Authoritative timeline (actual clip durations, overlaps subtracted)
    shots_present = [c["shot"] for c in clips]
    for c in clips:                                   # make sure overlap maths uses clamped values
        c["shot"]["trans_dur"] = c["trans_dur"] if c["transition"] in REAL_TRANSITIONS else c["shot"].get("trans_dur", 0.0)
    timeline = compute_timeline(shots_present, [c["dur"] for c in clips])
    expected = timeline_total(timeline)

    if out_file.exists() and not args.force:
        have = probe_video_info(out_file)["duration"]
        if abs(have - expected) < 0.2:
            print(f"  ⏭️  assembled_raw.mp4 exists ({have:.2f}s) — use --force to rebuild")
            project["timeline"] = timeline
            project["assembled_file"] = str(out_file)
            project["assembled_duration"] = round(have, 3)
            save_project(project, proj_path)
            return
        print(f"  ♻️  existing assembly is {have:.2f}s, expected {expected:.2f}s — rebuilding")

    n_trans = sum(1 for c in clips if c["transition"] in REAL_TRANSITIONS and c["trans_dur"] > 0)
    print(f"  {len(clips)} clips · {n_trans} transition(s) · expected {expected:.2f}s"
          + (f" · {dropped} shot(s) dropped" if dropped else ""))

    ok = build_single_pass(clips, out_file, tw, th, fps) if len(clips) > 1 else \
        run_ff(["ffmpeg", "-y", "-i", str(clips[0]["path"]), *encode_args(fps), str(out_file)], "single clip")
    if not ok and len(clips) > 1:
        print("  ⚠️  Single-pass graph failed — falling back to pairwise assembly")
        ok = build_iterative(clips, out_file, tw, th, fps)
    if not ok or not out_file.exists():
        sys.exit("\n  ❌  Assembly failed — see Output/logs/pipeline_ffmpeg.log")

    have = probe_video_info(out_file)["duration"]
    if abs(have - expected) > 0.25:
        print(f"  ⚠️  Assembled {have:.2f}s vs expected {expected:.2f}s — timeline re-based on the real file")
    project["timeline"] = timeline
    project["assembled_file"] = str(out_file)
    project["assembled_duration"] = round(have, 3)
    save_project(project, proj_path)

    print("\n  Timeline (assembled file):")
    for t in timeline:
        print(f"    {t['shot_id']}  {t['treatment']:<3} {t['start']:6.2f} → {t['end']:6.2f}s"
              + (f"   ⟶ {t['transition_out']} {t['trans_dur']}s" if t['trans_dur'] else ""))
    print(f"\n  ✅  assembled_raw.mp4  ({have:.2f}s)\n")


if __name__ == "__main__":
    main()
