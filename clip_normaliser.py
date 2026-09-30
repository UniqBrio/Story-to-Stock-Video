#!/usr/bin/env python3
"""
clip_normaliser.py  (SVOS v2 render layer)
──────────────────
Turns every shot into a clip of EXACTLY the planned duration at the target
size/fps, so the assembled timeline is deterministic (overlays and VO stay in sync).

  T2/T3 stock video  : trim → fill-crop → optional slow push (Ken Burns on video)
                       → per-shot grade → unified finishing grade. Short sources
                       hold their last frame (tpad) instead of shortening the shot.
  stock image        : Ken Burns zoompan (oversampled) → grades.
  T1 card            : flat colour frame (text is burned in by overlay_engine).
  T5 logo card       : brand colour frame + centred logo with a 0.5s fade/rise entrance.
  T4 product insert  : screen recording letterboxed on a blurred, darkened copy of
                       itself (no crop of UI), held 1–2s on the end state if short.

Output: Assets/Normalised/norm_<shot>.mp4 (video-only, yuv420p, same fps everywhere)

Usage:
    python clip_normaliser.py project.json
    python clip_normaliser.py project.json --shot 003
    python clip_normaliser.py project.json --force
"""

import sys
import argparse
from pathlib import Path

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, probe_video_info,
                         shot_kind, infer_asset_type, hex_clean, banner, set_log, assets_dir,
                         output_dir, ff_escape_path, BRAND)


# ── Per-shot emotional grades (story arc) ────────────────────────────────────
GRADE_FILTERS = {
    "chaos": "eq=saturation=0.72:brightness=-0.03:contrast=1.06,colorbalance=rs=-0.05:gs=-0.04:bs=0.08",
    "pivot": "eq=saturation=1.10:brightness=0.03:contrast=0.98,colorbalance=rm=0.05:gm=0.02:bm=-0.04,"
             "curves=r='0/0 0.5/0.55 1/1':g='0/0 0.5/0.52 1/1'",
    "cta":   "eq=saturation=1.06:contrast=1.02",
    "none":  "",
}

# ── ONE unified finishing grade across all stock (finishing-colorist) ────────
# Warm practicals preserved, soft cinematic contrast, protected skin, no teal-orange.
UNIFIED_GRADES = {
    "none":          "",
    "warm_soft":     "eq=contrast=1.03:saturation=0.96:gamma=1.02,"
                     "colorbalance=rm=0.025:gm=0.005:bm=-0.02:rh=0.01:bh=-0.015,"
                     "vignette=angle=PI/4.6:mode=forward",
    "clean_neutral": "eq=contrast=1.02:saturation=0.98,unsharp=3:3:0.25:3:3:0",
    "cool_calm":     "eq=contrast=1.02:saturation=0.94,colorbalance=rm=-0.02:bm=0.02,vignette=angle=PI/4.8",
}
GRAIN = "noise=alls=4:allf=t+u"     # fine temporal grain — felt, not seen


def base_fit(tw: int, th: int) -> str:
    return f"scale={tw}:{th}:force_original_aspect_ratio=increase:flags=lanczos,crop={tw}:{th},setsar=1"


def video_push(kb: str, dur: float, tw: int, th: int) -> str:
    """
    Slow push/pull on VIDEO via time-driven crop (zoompan is for stills).
    zoom_in: 1.00→1.07 · zoom_out: 1.07→1.00 · pan_left/right: 1.06 with lateral drift.
    Oversample first so the crop never goes below output resolution.
    """
    if kb not in ("zoom_in", "zoom_out", "pan_left", "pan_right"):
        return base_fit(tw, th)
    ow, oh = int(tw * 1.12), int(th * 1.12)
    d = max(dur, 0.1)
    if kb == "zoom_in":
        z = f"(1+0.07*min(t/{d:.3f}\\,1))"
    elif kb == "zoom_out":
        z = f"(1.07-0.07*min(t/{d:.3f}\\,1))"
    else:
        z = "1.06"
    w_expr = f"iw/{z}"
    h_expr = f"ih/{z}"
    if kb == "pan_left":
        x_expr = f"(iw-ow)*(1-min(t/{d:.3f}\\,1))"
    elif kb == "pan_right":
        x_expr = f"(iw-ow)*min(t/{d:.3f}\\,1)"
    else:
        x_expr = "(iw-ow)/2"
    return (f"scale={ow}:{oh}:force_original_aspect_ratio=increase:flags=lanczos,crop={ow}:{oh},"
            f"crop=w='{w_expr}':h='{h_expr}':x='{x_expr}':y='(ih-oh)/2',"
            f"scale={tw}:{th}:flags=lanczos,setsar=1")


def ken_burns_image(kb: str, duration: float, fps: int, tw: int, th: int) -> str:
    d = max(int(round(duration * fps)), 1)
    pre = f"scale={tw*2}:{th*2}:force_original_aspect_ratio=increase:flags=lanczos,crop={tw*2}:{th*2},setsar=1"
    if kb == "zoom_in":
        z, x, y = f"'1.0+0.18*on/{d}'", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif kb == "zoom_out":
        z, x, y = f"'1.18-0.18*on/{d}'", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif kb == "pan_left":
        z, x, y = "1.10", f"'(iw-iw/zoom)*(1-on/{d})'", "ih/2-(ih/zoom/2)"
    elif kb == "pan_right":
        z, x, y = "1.10", f"'(iw-iw/zoom)*on/{d}'", "ih/2-(ih/zoom/2)"
    else:
        return base_fit(tw, th)
    return f"{pre},zoompan=z={z}:x={x}:y={y}:d={d}:s={tw}x{th}:fps={fps},setsar=1"


def encode_args(fps: int) -> list[str]:
    return ["-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "16",
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-an", "-movflags", "+faststart"]


# ── Normalise a single shot ───────────────────────────────────────────────────
def normalise_shot(shot: dict, project: dict, out_dir: Path, force: bool) -> tuple[Path | None, str]:
    tw, th, fps = project.get("width", 1080), project.get("height", 1920), project.get("fps", 30)
    render = project.get("render", {})
    text_cfg = project.get("text", {})
    logo_cfg = project.get("logo", {})
    sid      = shot["shot_id"]
    kind     = shot_kind(shot)
    duration = float(shot.get("duration", 4.0))
    out_file = out_dir / f"norm_{sid}.mp4"

    if out_file.exists() and not force:
        info = probe_video_info(out_file)
        if abs(info["duration"] - duration) < 0.15 and info["width"] == tw and info["height"] == th:
            print(f"    ⏭️  {sid} — already normalised ({info['duration']:.2f}s)")
            return out_file, "skip"
        print(f"    ♻️  {sid} — existing clip is {info['duration']:.2f}s / {info['width']}×{info['height']}, re-rendering")

    unified = UNIFIED_GRADES.get(render.get("unified_grade", "warm_soft"), "")
    grain   = GRAIN if render.get("film_grain") else ""
    grade   = GRADE_FILTERS.get(shot.get("color_grade", "none"), "")
    finishing = ",".join(f for f in (grade, unified, grain) if f)

    # ── T1 blank text frame ───────────────────────────────────────────────────
    if kind == "card":
        bg = hex_clean(shot.get("card_bg") or text_cfg.get("card_bg"), BRAND["offwhite"])
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s={tw}x{th}:r={fps}:d={duration:.3f}",
               "-t", f"{duration:.3f}", "-vf", "format=yuv420p", *encode_args(fps), str(out_file)]
        ok = run_ff(cmd, f"{sid} card")
        if ok:
            print(f"    ✅  {sid} T1 card {bg} → {out_file.name}  ({duration:.1f}s)")
        return (out_file if ok else None), "ok" if ok else "error"

    # ── T5 logo card ──────────────────────────────────────────────────────────
    if kind == "logo_card":
        bg = hex_clean(shot.get("card_bg") or logo_cfg.get("card_bg"), BRAND["purple"])
        logo = shot.get("local_file") or logo_cfg.get("path", "")
        if not logo or not Path(logo).exists():
            print(f"    ⚠️  {sid} — logo file missing ({logo}); rendering a plain brand frame")
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s={tw}x{th}:r={fps}:d={duration:.3f}",
                   "-t", f"{duration:.3f}", "-vf", "format=yuv420p", *encode_args(fps), str(out_file)]
        else:
            lw = int(tw * float(logo_cfg.get("card_scale", 0.42)))
            # Logo lands slightly above centre so the CTA text sits below it inside the safe zone.
            y_final = f"(H*0.44-h/2)"
            rise = f"{y_final}+36*pow(1-min(t/0.5\\,1)\\,2)"
            fc = (f"[1:v]format=rgba,scale={lw}:-1:flags=lanczos,fade=t=in:st=0:d=0.5:alpha=1[logo];"
                  f"[0:v][logo]overlay=x=(W-w)/2:y='{rise}':format=auto,format=yuv420p[v]")
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s={tw}x{th}:r={fps}:d={duration:.3f}",
                   "-loop", "1", "-t", f"{duration:.3f}", "-i", logo,
                   "-filter_complex", fc, "-map", "[v]", "-t", f"{duration:.3f}", *encode_args(fps), str(out_file)]
        ok = run_ff(cmd, f"{sid} logo card")
        if ok:
            print(f"    ✅  {sid} T5 logo card {bg} → {out_file.name}  ({duration:.1f}s)")
        return (out_file if ok else None), "ok" if ok else "error"

    # ── Everything else needs a source file ───────────────────────────────────
    local_file = shot.get("local_file", "")
    if not local_file or not Path(local_file).exists():
        print(f"    ⚠️  {sid} — source file missing: {local_file or '(none)'}")
        return None, "missing"
    asset_type = shot.get("asset_type") or infer_asset_type(local_file)
    if asset_type not in ("video", "image"):
        asset_type = infer_asset_type(local_file)
    if not asset_type:
        print(f"    ⚠️  {sid} — cannot tell if '{Path(local_file).suffix}' is video or image")
        return None, "error"
    src = Path(local_file)
    trim_in  = max(0.0, float(shot.get("trim_in", 0.0)))
    kb       = shot.get("ken_burns", "none")

    # ── T4 product insert ─────────────────────────────────────────────────────
    if kind == "product":
        if asset_type == "video":
            info = probe_video_info(src)
            avail = max(0.0, info["duration"] - trim_in)
            take = min(duration, avail) if avail > 0.3 else duration
            hold = max(0.0, duration - take)
            inp = ["-ss", f"{trim_in:.3f}", "-i", str(src), "-t", f"{take:.3f}"]
            hold_f = f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold > 0.02 else ""
            if hold > 0.02:
                print(f"    ℹ️  {sid} — demo segment is {take:.1f}s; holding end state {hold:.1f}s")
        else:
            inp = ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(src)]
            hold_f = ""
        fg_w = int(tw * 0.92)
        vf = (f"split[bg][fg];"
              f"[bg]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
              f"gblur=sigma=42,eq=brightness=-0.18:saturation=0.8[bgb];"
              f"[fg]scale={fg_w}:-2:flags=lanczos[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}{hold_f},"
              f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,format=yuv420p[v]")
        cmd = ["ffmpeg", "-y", *inp, "-filter_complex", vf, "-map", "[v]", "-t", f"{duration:.3f}",
               *encode_args(fps), str(out_file)]
        ok = run_ff(cmd, f"{sid} product insert")
        if ok:
            print(f"    ✅  {sid} T4 product insert → {out_file.name}  ({duration:.1f}s)")
        return (out_file if ok else None), "ok" if ok else "error"

    # ── Stock video ───────────────────────────────────────────────────────────
    if asset_type == "video":
        info = probe_video_info(src)
        src_dur = info["duration"]
        if src_dur <= 0:
            src_dur = trim_in + duration
        if trim_in >= src_dur - 0.3:
            print(f"    ⚠️  {sid} — trim_in {trim_in}s is past the end of a {src_dur:.1f}s clip; using 0")
            trim_in = 0.0
        avail = src_dur - trim_in
        take  = min(duration, avail)
        hold  = max(0.0, duration - take)
        if hold > 0.05:
            print(f"    ⚠️  {sid} — source only {avail:.1f}s from trim_in; holding last frame {hold:.1f}s "
                  f"(pick a longer clip at G4 if this shows)")
        hold_f = f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold > 0.02 else ""
        vf = ",".join(p for p in [
            video_push(kb, duration, tw, th),
            f"fps={fps}",
            finishing,
            f"trim=duration={duration:.3f}{hold_f}" if not hold_f else f"{hold_f[1:]},trim=duration={duration:.3f}",
            "setpts=PTS-STARTPTS",
            "format=yuv420p",
        ] if p)
        cmd = ["ffmpeg", "-y", "-ss", f"{trim_in:.3f}", "-i", str(src), "-t", f"{take + 0.05:.3f}",
               "-vf", vf, "-t", f"{duration:.3f}", *encode_args(fps), str(out_file)]
        ok = run_ff(cmd, f"{sid} video normalise")
        if ok:
            print(f"    ✅  {sid} video{'+' + kb if kb != 'none' else ''} → {out_file.name}  "
                  f"({duration:.1f}s, grade={shot.get('color_grade','none')}+{render.get('unified_grade','warm_soft')})")
        return (out_file if ok else None), "ok" if ok else "error"

    # ── Stock image ───────────────────────────────────────────────────────────
    if kb == "none":
        print(f"    ℹ️  {sid} — still image with Ken Burns 'none'; a slow zoom_in reads better on Reels")
    vf = ",".join(p for p in [
        "format=rgba,split[a][b];[b]drawbox=c=black@1:t=fill[bg];[bg][a]overlay=format=auto",  # flatten alpha
        ken_burns_image(kb, duration, fps, tw, th),
        "unsharp=3:3:0.3:3:3:0" if kb != "none" else "",
        finishing,
        f"trim=duration={duration:.3f},setpts=PTS-STARTPTS",
        "format=yuv420p",
    ] if p)
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(src),
           "-vf", vf, "-t", f"{duration:.3f}", *encode_args(fps), str(out_file)]
    ok = run_ff(cmd, f"{sid} image → video")
    if ok:
        print(f"    ✅  {sid} image+{kb} → {out_file.name}  ({duration:.1f}s)")
    return (out_file if ok else None), "ok" if ok else "error"


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Normalise clips per shot plan")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--shot",  help="Process a single shot ID only")
    ap.add_argument("--force", action="store_true", help="Re-render even if the normalised file exists")
    ap.add_argument("--allow-drop", action="store_true",
                    help="Continue when a shot has no source (the shot is dropped and the video gets shorter)")
    args = ap.parse_args()

    check_ffmpeg()
    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    shots = project["shots"]
    tw, th, fps = project.get("width", 1080), project.get("height", 1920), project.get("fps", 30)
    norm_dir = assets_dir(project, proj_path) / "Normalised"
    norm_dir.mkdir(parents=True, exist_ok=True)

    targets = shots
    if args.shot:
        targets = [s for s in shots if s["shot_id"] == args.shot]
        if not targets:
            sys.exit(f"❌  Shot '{args.shot}' not found in project.json")

    banner("Clip Normaliser", f"{len(targets)} shots  |  {tw}×{th} @ {fps}fps  |  unified grade: "
           f"{project.get('render', {}).get('unified_grade', 'warm_soft')}", f"Output: {norm_dir}")

    norm_map = dict(project.get("normalised_map", {}))
    ok = err = 0
    missing = []
    for shot in targets:
        result, status = normalise_shot(shot, project, norm_dir, args.force)
        if result:
            norm_map[shot["shot_id"]] = str(result)
            shot["normalised_file"] = str(result)
            shot["actual_duration"] = round(probe_video_info(result)["duration"], 3)
            ok += 1
        elif status == "missing":
            norm_map.pop(shot["shot_id"], None)       # never let a stale render stand in for a missing source
            shot.pop("normalised_file", None)
            missing.append(shot["shot_id"])
        else:
            err += 1

    project["normalised_map"] = norm_map
    save_project(project, proj_path)

    print(f"\n{'═'*62}\n  {'✅' if not (missing or err) else '❌'}  {ok} normalised  |  "
          f"{len(missing)} missing source  |  {err} errors")
    if missing:
        print(f"  Missing source: {', '.join(missing)}")
        if args.allow_drop:
            print("  ⚠️  --allow-drop: these shots are DROPPED and the video will be shorter than the plan")
        else:
            print("  ❌  Refusing to continue — a dropped shot shortens the picture and desyncs the VO.\n"
                  "     Fix the assets (validation_report.py / prompt_generator.py) or rerun with --allow-drop")
    print(f"  normalised_map saved to project.json\n{'═'*62}\n")
    if err or (missing and not args.allow_drop):
        sys.exit(1)


if __name__ == "__main__":
    main()
