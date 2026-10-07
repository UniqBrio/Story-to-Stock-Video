#!/usr/bin/env python3
"""
audio_mixer.py  (SVOS v2 render layer)
──────────────
Layers voiceover · music · BGM · end sting onto the overlaid video, then
normalises the mix to the target loudness.

Fixes vs v1
  • amix no longer attenuates every track by 1/N (normalize=0) — the VO used to
    drop ~10 dB the moment music was present.
  • DUCK MAP: speech regions are detected on the VO (silencedetect) and music /
    BGM are ducked by `duck_db` under every spoken phrase with 150 ms attack and
    350 ms release. The map is written to project.json for the Production Bible.
  • Two-pass EBU R128 loudness normalisation to `target_lufs` (default −14 LUFS,
    true peak −1.5 dBTP — sonic-brand-identity / Instagram).
  • End sting placed so it lands with the logo card; VO may be offset (vo_start).
  • 48 kHz stereo throughout.

Output: Assets/Assembled/assembled_with_audio.mp4

Usage:
    python audio_mixer.py project.json
    python audio_mixer.py project.json --force
"""

import re
import sys
import json
import argparse
import tempfile
from pathlib import Path

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, run_ff_capture,
                         probe_video_info, probe_duration, banner, set_log, output_dir, variant_suffix)


# ── Duck map from the VO ──────────────────────────────────────────────────────
def detect_speech(vo_path: str, offset: float, total: float,
                  noise_db: float = -35.0, min_silence: float = 0.30) -> list[tuple[float, float]]:
    """Speech segments (absolute video time) = complement of detected silences."""
    vo_dur = probe_duration(vo_path)
    rc, err = run_ff_capture(["ffmpeg", "-hide_banner", "-nostats", "-i", vo_path,
                              "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
                             "silencedetect (VO)")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", err)]
    ends   = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", err)]
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else vo_dur
        silences.append((s, e))
    speech, cursor = [], 0.0
    for s, e in sorted(silences):
        if s - cursor > 0.12:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if vo_dur - cursor > 0.12:
        speech.append((cursor, vo_dur))
    # merge tiny gaps, shift by VO offset, clamp to the video
    merged: list[list[float]] = []
    for a, b in speech:
        a, b = a + offset, b + offset
        if b <= 0 or a >= total:
            continue
        a, b = max(0.0, a), min(total, b)
        if merged and a - merged[-1][1] < 0.35:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= 0.15]


def duck_expression(segments: list[tuple[float, float]], duck_db: float,
                    attack: float = 0.15, release: float = 0.35) -> str:
    """volume expression: 1 outside speech, 10^(-duck/20) inside, with ramps."""
    if not segments or duck_db <= 0:
        return ""
    g = 10 ** (-duck_db / 20.0)
    envs = []
    for a, b in segments:
        envs.append(f"min(max((t-{a - attack:.3f})/{attack:.3f}\\,0)\\,1)*min(max(({b + release:.3f}-t)/{release:.3f}\\,0)\\,1)")
    env = envs[0]
    for e in envs[1:]:
        env = f"max({env}\\,{e})"
    return f"1-{1 - g:.4f}*({env})"


def measure_loudness(path: str, target_i: float, tp: float, lra: float = 11.0) -> dict | None:
    rc, err = run_ff_capture(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                              "-af", f"loudnorm=I={target_i}:TP={tp}:LRA={lra}:print_format=json",
                              "-f", "null", "-"], "loudnorm measure")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def mix(project: dict, video_path: Path, out_path: Path, tmp: Path) -> dict | None:
    a = project.get("audio", {})
    total = probe_video_info(video_path)["duration"] or float(project.get("assembled_duration") or project.get("total_duration", 0))
    if total <= 0:
        sys.exit("❌  Could not determine video duration")
    target_i, tp = float(a.get("target_lufs", -14.0)), float(a.get("true_peak", -1.5))
    duck_db = float(a.get("duck_db", 8.0))
    vo_start = float(a.get("vo_start", 0.0))
    report = {"video_duration": round(total, 3), "layers": [], "duck_segments": [], "target_lufs": target_i, "true_peak": tp}

    inputs, graph, mix_labels = ["-i", str(video_path)], [], []
    idx = 1

    def layer(path_key: str) -> str:
        p = a.get(path_key, "")
        if p and Path(p).exists():
            return p
        if p:
            print(f"    ⚠️  {path_key} not found: {p}")
        return ""

    vo, music, bgm, sting = layer("vo_path"), layer("music_path"), layer("bgm_path"), layer("sting_path")

    # Voiceover timing plan from vo_aligner: each phrase is cut from the (cleared, untouched) VO file and
    # placed under its shot. Ignored if the VO file changed since the plan was made.
    plan = project.get("vo_plan") or {}
    placed = plan.get("placements") if (vo and plan.get("source") == vo and
                                        abs(plan.get("source_mtime", 0) - Path(vo).stat().st_mtime) < 1) else None

    # Speech map first — it drives the ducking on the music layers
    if placed:
        segments = [(round(p["at"], 3), round(min(total, p["at"] + p["src_end"] - p["src_start"]), 3))
                    for p in placed if p["at"] < total]
    else:
        segments = detect_speech(vo, vo_start, total) if vo else []
    duck = duck_expression(segments, duck_db) if (vo and (music or bgm)) else ""
    report["duck_segments"] = segments
    report["duck_db"] = duck_db if duck else 0

    if vo and placed:
        n = len(placed)
        vol = float(a.get("vo_volume", 1.0))
        chain = [f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol:.2f},asplit={n}" +
                 "".join(f"[vs{k}]" for k in range(n))]
        for k, p in enumerate(placed):
            d = p["src_end"] - p["src_start"]
            s0, s1 = max(0.0, p["src_start"] - 0.06), p["src_end"] + 0.12          # keep consonant edges
            chain.append(f"[vs{k}]atrim={s0:.3f}:{s1:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.03,afade=t=out:st={max(0.0, s1 - s0 - 0.05):.3f}:d=0.05,"
                         f"adelay={int(max(0.0, p['at'] - 0.06) * 1000)}:all=1[vp{k}]")
        chain.append("".join(f"[vp{k}]" for k in range(n)) +
                     f"amix=inputs={n}:normalize=0:dropout_transition=0,apad,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[vo]")
        graph.extend(chain)
        inputs += ["-i", vo]; mix_labels.append("[vo]"); idx += 1
        report["layers"].append({"vo": Path(vo).name, "aligned_phrases": n, "speech_segments": len(segments)})
        print(f"    🎙  VO   {Path(vo).name}  ({n} phrases placed on their shots → duck {duck_db:g} dB)")
    elif vo:
        vo_dur = probe_duration(vo)
        if vo_dur + vo_start > total + 0.3:
            print(f"    ⚠️  VO is {vo_dur:.1f}s (+{vo_start}s offset) but the video is {total:.1f}s — VO will be cut")
        delay = int(max(0.0, vo_start) * 1000)
        graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(a.get('vo_volume', 1.0)):.2f},"
                     f"adelay={delay}:all=1,apad,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[vo]")
        inputs += ["-i", vo]; mix_labels.append("[vo]"); idx += 1
        report["layers"].append({"vo": Path(vo).name, "start": vo_start, "speech_segments": len(segments)})
        print(f"    🎙  VO   {Path(vo).name}  ({len(segments)} spoken phrases → duck {duck_db:g} dB)")

    def music_layer(path: str, label: str, vol: float, fade_in: float, fade_out: float):
        nonlocal idx
        inputs.extend(["-stream_loop", "-1", "-i", path])
        fo_start = max(0.0, total - fade_out)
        chain = (f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                 f"volume={vol:.2f},afade=t=in:st=0:d={fade_in:.2f},afade=t=out:st={fo_start:.3f}:d={fade_out:.2f}")
        if duck:
            chain += f",volume='{duck}':eval=frame"
        graph.append(chain + f"[{label}]")
        mix_labels.append(f"[{label}]"); idx += 1

    if music:
        music_layer(music, "music", float(a.get("music_volume", 0.8)), float(a.get("music_fade_in", 0.5)), float(a.get("music_fade_out", 2.0)))
        report["layers"].append({"music": Path(music).name, "volume": a.get("music_volume", 0.8)})
        print(f"    ♪   Music {Path(music).name}  vol={a.get('music_volume', 0.8)}")
    if bgm:
        music_layer(bgm, "bgm", float(a.get("bgm_volume", 0.4)), 1.0, 2.5)
        report["layers"].append({"bgm": Path(bgm).name, "volume": a.get("bgm_volume", 0.4)})
        print(f"    ♪   BGM   {Path(bgm).name}  vol={a.get('bgm_volume', 0.4)}")
    if sting:
        s_dur = probe_duration(sting)
        # land the sting so it ENDS 0.4s before the video ends (300–500 ms clean tail, sonic-brand-identity §7)
        at = max(0.0, total - s_dur - 0.4)
        graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(a.get('sting_volume', 0.9)):.2f},"
                     f"adelay={int(at * 1000)}:all=1,apad,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[sting]")
        inputs += ["-i", sting]; mix_labels.append("[sting]"); idx += 1
        report["layers"].append({"sting": Path(sting).name, "at": round(at, 3)})
        print(f"    ✦   Sting {Path(sting).name}  at {at:.2f}s")

    if not mix_labels:
        print("    ℹ️  No audio layers — writing a silent track")
        ok = run_ff(["ffmpeg", "-y", "-i", str(video_path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                     "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                     "-movflags", "+faststart", str(out_path)], "silent track")
        return report if ok else None

    if len(mix_labels) == 1:
        graph.append(f"{mix_labels[0]}acopy[mix]")
    else:
        graph.append("".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=longest:normalize=0:dropout_transition=0[mix]")

    # Pass 1 — render the mix to WAV
    mix_wav = tmp / "mix.wav"
    if not run_ff(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[mix]",
                   "-t", f"{total:.3f}", "-c:a", "pcm_s16le", "-ar", "48000", str(mix_wav)], "audio mix"):
        return None
    # Pass 2 — measure
    meas = measure_loudness(str(mix_wav), target_i, tp)
    if meas:
        report["measured_input"] = {k: meas.get(k) for k in ("input_i", "input_tp", "input_lra", "input_thresh")}
        print(f"    📏  mix measured {float(meas['input_i']):.1f} LUFS / TP {float(meas['input_tp']):.1f} dBTP → target {target_i} LUFS")
        ln = (f"loudnorm=I={target_i}:TP={tp}:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
              f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
              f":offset={meas.get('target_offset', 0)}:linear=true:print_format=summary")
    else:
        print("    ⚠️  loudness measurement failed — using single-pass loudnorm")
        ln = f"loudnorm=I={target_i}:TP={tp}:LRA=11"
    # Pass 3 — normalise + mux
    ok = run_ff(["ffmpeg", "-y", "-i", str(video_path), "-i", str(mix_wav),
                 "-filter_complex", f"[1:a]{ln},aresample=48000[a]", "-map", "0:v:0", "-map", "[a]",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                 "-t", f"{total:.3f}", "-movflags", "+faststart", str(out_path)], "loudnorm + mux")
    return report if ok else None


def main():
    ap = argparse.ArgumentParser(description="Mix VO, music, BGM and sting onto the video with ducking + loudness normalisation")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    check_ffmpeg()
    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    overlaid = project.get("overlaid_file", "")
    if not overlaid or not Path(overlaid).exists():
        sys.exit("❌  overlaid_file not found in project.json — run overlay_engine.py first")
    out_file = Path(overlaid).parent / f"assembled_with_audio{variant_suffix(project)}.mp4"

    banner("Audio Mixer", f"target {project.get('audio', {}).get('target_lufs', -14)} LUFS · "
           f"TP {project.get('audio', {}).get('true_peak', -1.5)} dBTP · duck {project.get('audio', {}).get('duck_db', 8)} dB")

    if out_file.exists() and not args.force:
        print("  ⏭️  assembled_with_audio.mp4 exists — use --force to rebuild")
        project["audio_mixed_file"] = str(out_file)
        save_project(project, proj_path)
        return

    # U-rated gate: every audio layer (VO, music, BGM, sting) must have passed the content-safety check
    import content_safety as safety
    blocked = []
    for k in ("vo_path", "music_path", "bgm_path", "sting_path"):
        p = project.get("audio", {}).get(k, "")
        if p and Path(p).exists() and safety.require_audio(project, p):
            blocked.append(f"{k}: {safety.require_audio(project, p)}")
    if blocked:
        print("  🛡   Not mixed — audio not cleared:\n" + "\n".join(f"     • {b}" for b in blocked))
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="svos_audio_") as tmp:
        report = mix(project, Path(overlaid), out_file, Path(tmp))
    if not report:
        sys.exit("  ❌  Audio mix failed — see Output/logs/pipeline_ffmpeg.log")

    project["audio_mixed_file"] = str(out_file)
    project["audio_report"] = report
    save_project(project, proj_path)
    dur = probe_video_info(out_file)["duration"]
    print(f"\n  ✅  assembled_with_audio.mp4  ({dur:.2f}s)\n")


if __name__ == "__main__":
    main()
