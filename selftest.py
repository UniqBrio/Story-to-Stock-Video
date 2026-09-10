#!/usr/bin/env python3
"""
selftest.py  (SVOS v2 render layer)
───────────
End-to-end smoke test with SYNTHETIC assets — no API keys, no downloads.
Builds a 7-shot story that exercises every treatment and every fix:

    001 T3 landscape 1920×1080 video → portrait crop + zoom_in + keyword overlay
    002 T1 off-white card, ₹ glyph, 8-word line, dissolve out
    003 T2 3s source under a 4s shot → last-frame hold
    004 T3 still image + Ken Burns + brand-orange keyword
    005 T4 16:9 screen recording → blurred letterbox; its overlay must be DROPPED
    006 T2 Tamil overlay (Nirmala/Noto font), fade_white out
    007 T5 purple logo card + CTA "DM 'BRIO'"
    + VO with speech-like bursts (duck map), real bgm.mp3 if present, sting, logo bug

Then runs run_pipeline.py --skip-fetch --force and asserts on the result.

Usage:
    python selftest.py                 # → ./_selftest/
    python selftest.py --dir C:/tmp/svos_test
    python selftest.py --keep          # keep the generated folder
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path

import openpyxl
from svos_common import probe_video_info, check_ffmpeg

HERE = Path(__file__).parent


def ff(*args) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"❌  asset generation failed: {' '.join(args)}\n{r.stderr[-800:]}")


def make_assets(d: Path) -> dict:
    a = d / "assets"
    a.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "6", "-pix_fmt", "yuv420p", str(a / "clip_landscape.mp4"))
    ff("-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30", "-t", "3", "-pix_fmt", "yuv420p", str(a / "clip_short.mp4"))
    ff("-f", "lavfi", "-i", "smptehdbars=size=1080x1920:rate=30", "-t", "6", "-pix_fmt", "yuv420p", str(a / "clip_bars.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "5", "-pix_fmt", "yuv420p", str(a / "demo_screen.mp4"))
    ff("-f", "lavfi", "-i", "gradients=size=1080x1920:speed=0.01", "-frames:v", "1", str(a / "still.png"))
    # "speech": 220 Hz bursts 1.8s on / 1.2s off for 24s
    ff("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=24",
       "-af", "volume='if(lt(mod(t\\,3)\\,1.8)\\,0.6\\,0)':eval=frame", str(a / "vo.wav"))
    ff("-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=0.8", "-af", "afade=t=out:st=0.4:d=0.4,volume=0.5", str(a / "sting.wav"))
    bgm_src = HERE / "Output" / "bgm.mp3"
    if bgm_src.exists():
        shutil.copy2(bgm_src, a / "bgm.mp3")
    else:
        ff("-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000:duration=40", "-af", "volume=0.3", str(a / "bgm.mp3"))
    # Brand logo: set SVOS_LOGO_PATH to your own file, or drop one at ./assets/logo.png.
    # If neither is present, a plain wordmark is generated below.
    logo_candidates = [
        Path(c) for c in (os.environ.get("SVOS_LOGO_PATH", ""), str(HERE / "assets" / "logo.png")) if c
    ]
    logo = next((p for p in logo_candidates if p.exists()), None)
    if logo:
        shutil.copy2(logo, a / "logo.png")
    else:
        ff("-f", "lavfi", "-i", "color=c=white@0.0:s=800x260,format=rgba", "-frames:v", "1",
           "-vf", "drawtext=text='UniqBrio':fontsize=150:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2", str(a / "logo.png"))
    return {k: str(a / v) for k, v in {
        "landscape": "clip_landscape.mp4", "short": "clip_short.mp4", "bars": "clip_bars.mp4",
        "demo": "demo_screen.mp4", "still": "still.png", "vo": "vo.wav", "sting": "sting.wav",
        "bgm": "bgm.mp3", "logo": "logo.png"}.items()}


def make_xlsx(d: Path, A: dict) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Shot Plan"
    ws.append(["SVOS v2 self-test — Shot Plan"])
    ws.append(["SHOT IDENTITY", None, None, None, "ASSET SOURCING", None, None, None, "TRIM & GRADE", None, None, None,
               "TEXT OVERLAY", None, None, None, "TRANSITION", None, "AUDIO", "STATUS", "TREATMENT & DESIGN (v2)"])
    ws.append(["Shot ID", "Scene Description", "Duration (s)", "Search Keywords", "Asset Priority", "Asset Type", "Source",
               "Local File Path", "Trim In (s)", "Trim Out (s)", "Color Grade", "Ken Burns", "Text Overlay", "Text Start (s)",
               "Text End (s)", "Text Position", "Transition Out", "Trans Dur (s)", "VO File Path", "Status",
               "Treatment", "Text Style", "Text Anim", "Text Color", "Card BG", "Logo Bug", "Font Size", "VO Line", "Notes"])
    rows = [
        [1, "Owner buried in admin at night", 4, "stressed teacher desk", "video_first", "video", "local", A["landscape"], 0.5, 4.5,
         "chaos", "zoom_in", "11:41 PM. Sunday.", 0.4, 3.4, "lower_third", "cut", 0, "", "downloaded", "T3", "keyword", "rise", "", "", "auto", "", "It is 11:41 on a Sunday night", ""],
        [2, "Card: the number that hurts", 3, "", "image_only", "", "", "", 0, 3, "none", "none",
         "₹47,000. Gone. Every single year.", 0.2, 2.9, "center", "dissolve", 0.5, "", "pending", "T1", "card", "rise", "", "", "auto", "", "Forty-seven thousand rupees. Gone. Every year.", "core claim"],
        [3, "Owner on the phone chasing fees", 4, "phone call stress", "video_first", "video", "local", A["short"], 0, 4,
         "chaos", "none", "", 0, 0, "center", "cut", 0, "", "downloaded", "T2", "auto", "", "", "", "auto", "", "Fees. Schedules. WhatsApp.", "source only 3s → hold"],
        [4, "Calm desk, dashboard, confidence", 3, "happy teacher relaxed", "image_first", "image", "local", A["still"], 0, 3,
         "pivot", "zoom_in", "Confidence", 0.3, 2.6, "center", "cut", 0, "", "downloaded", "T3", "keyword", "pop", "#DE7D14", "", "auto", "", "...be confident in whatever you do", "anchor word"],
        [5, "Attendance module — one tap", 4, "", "video_first", "video", "demo-library", A["demo"], 0, 4,
         "none", "none", "SHOULD BE DROPPED", 0.3, 3.0, "center", "cut", 0, "", "downloaded", "T4", "auto", "", "", "", "auto", "", "attendance in one tap", "product proof"],
        [6, "Owner smiling, evening light", 3, "smiling teacher evening", "video_first", "video", "local", A["bars"], 1, 4,
         "pivot", "pan_right", "நம்பிக்கை", 0.3, 2.4, "center", "fade_white", 0.5, "", "downloaded", "T3", "keyword", "rise", "", "", "auto", "", "nambikkai", "Tamil glyph test"],
        [7, "Logo card — UniqBrio CTA", 5, "", "image_only", "", "", A["logo"], 0, 5,
         "cta", "none", "DM 'BRIO' to see it live", 0.9, 4.8, "center", "none", 0, "", "pending", "T5", "cta", "rise", "", "", "no", "", "DM BRIO", "single CTA"],
    ]
    for r in rows:
        ws.append(r)
    ps = wb.create_sheet("Project Settings")
    ps.append(["Project Settings", None, None])
    ps.append(["Setting", "Value", "Notes"])
    for k, v in [
        ("project_name", "SVOS Selftest"), ("output_folder", str(d / "Output")), ("output_filename", "selftest_final.mp4"),
        ("output_width", "1080"), ("output_height", "1920"), ("fps", "30"),
        ("music_path", ""), ("bgm_path", A["bgm"]), ("bgm_volume", "0.5"), ("vo_path", A["vo"]), ("vo_volume", "1.0"),
        ("sting_path", A["sting"]), ("logo_path", A["logo"]), ("logo_position", "top_right"), ("logo_scale", "0.12"),
        ("logo_opacity", "0.9"), ("logo_mode", "bug"), ("assets_folder", str(d / "Assets")),
        ("crf", "20"), ("preset", "veryfast"), ("text_font", "Inter"), ("text_size_default", "72"), ("text_shadow", "true"),
        ("unified_grade", "warm_soft"), ("target_lufs", "-14"), ("duck_db", "9"), ("platform", "reels"),
        ("pexels_api_key", ""), ("pixabay_api_key", ""), ("unsplash_api_key", ""),
    ]:
        ps.append([k, v, ""])
    dl = wb.create_sheet("Download Log")
    dl.append(["Download Log"]); dl.append(["Timestamp", "Shot ID", "Scene", "Source", "Asset URL", "Local Path", "Resolution", "Status"])
    at = wb.create_sheet("Asset Tracker")
    at.append(["Asset Tracker"]); at.append(["Shot ID", "Asset Type", "Source", "Resolution", "Duration (s)", "License", "Local Path", "Used In Output", "Score", "Duplicate", "Notes"])
    p = d / "story_plan_selftest.xlsx"
    wb.save(p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(HERE / "_selftest"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    check_ffmpeg()
    d = Path(args.dir)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)

    print("\n══ SVOS v2 self-test ══\n  generating synthetic assets...")
    A = make_assets(d)
    xlsx = make_xlsx(d, A)
    print(f"  story plan → {xlsx}\n  running pipeline (phases 1,3–8)...\n")
    rc = subprocess.run([sys.executable, str(HERE / "run_pipeline.py"), str(xlsx), "--skip-fetch", "--force"]).returncode

    proj = json.loads((d / "project.json").read_text(encoding="utf-8-sig"))
    final = Path(proj.get("final_output", ""))
    fails = []
    def check(cond, msg):
        print(("  ✅ " if cond else "  ❌ ") + msg)
        if not cond:
            fails.append(msg)

    print("\n══ assertions ══")
    check(final.exists(), f"final export exists ({final.name})")
    if final.exists():
        info = probe_video_info(final)
        expected = 26.0 - 1.0     # 7 shots = 26s, two 0.5s transitions overlap
        check(abs(info["duration"] - expected) < 0.35, f"duration {info['duration']:.2f}s ≈ {expected:.1f}s (transition overlaps respected)")
        check((info["width"], info["height"]) == (1080, 1920), f"resolution {info['width']}×{info['height']}")
        check(info["has_audio"] and info["sample_rate"] == 48000, f"audio {info['audio_codec']} {info['sample_rate']} Hz")
    tl = proj.get("timeline", [])
    check(len(tl) == 7, f"timeline has {len(tl)} shots")
    if len(tl) == 7:
        check(abs(tl[2]["start"] - 6.5) < 0.05, f"shot 003 starts at {tl[2]['start']}s (7.0 − 0.5 dissolve)")
        check(abs(tl[6]["start"] - 20.0) < 0.05, f"shot 007 starts at {tl[6]['start']}s (both overlaps subtracted)")
    man = proj.get("overlay_manifest", [])
    ids = [m["shot_id"] for m in man]
    check("005" not in ids, "T4 product insert overlay was dropped")
    check(set(ids) == {"001", "002", "004", "006", "007"}, f"overlays rendered for {ids}")
    tam = next((m for m in man if m["shot_id"] == "006"), None)
    check(bool(tam and tam["tamil"] and tam["font"] != "ffmpeg-default"), f"Tamil overlay uses a Tamil font ({tam['font'] if tam else '-'})")
    card = next((m for m in man if m["shot_id"] == "002"), None)
    check(bool(card and card["style"] == "card" and card["color"].upper() == "#111114"), "T1 card text is near-black on the off-white card")
    ar = proj.get("audio_report", {})
    check(len(ar.get("duck_segments", [])) >= 5, f"duck map found {len(ar.get('duck_segments', []))} spoken phrases")
    check(any("sting" in l for l in ar.get("layers", [])), "end sting placed")
    mf = Path(proj.get("render_manifest", ""))
    if mf.exists():
        m = json.loads(mf.read_text(encoding="utf-8"))
        li = m.get("loudness", {}).get("integrated_lufs")
        check(li is not None and abs(li - (-14)) < 1.5, f"integrated loudness {li} LUFS (target −14 ±1.5)")
        tp = m.get("loudness", {}).get("true_peak_dbtp")
        check(tp is not None and tp <= -1.0, f"true peak {tp} dBTP")
    check((d / "Output" / "qa_report.md").exists(), "qa_report.md written")
    check((d / "Output" / "selftest_final_cover.jpg").exists(), "cover frame written")
    norm5 = probe_video_info(proj["normalised_map"]["005"]) if "005" in proj.get("normalised_map", {}) else {}
    check(abs(norm5.get("duration", 0) - 4.0) < 0.1, f"product insert normalised to {norm5.get('duration', 0):.2f}s")
    norm3 = probe_video_info(proj["normalised_map"]["003"]) if "003" in proj.get("normalised_map", {}) else {}
    check(abs(norm3.get("duration", 0) - 4.0) < 0.1, f"3s source held to {norm3.get('duration', 0):.2f}s shot")

    print(f"\n{'✅  SELFTEST PASSED' if not fails else '❌  SELFTEST FAILED: ' + str(len(fails)) + ' check(s)'}  (pipeline rc={rc})")
    print(f"   folder: {d}\n")
    if not args.keep and not fails:
        pass  # keep by default so the user can open the video; use --keep to be explicit
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
