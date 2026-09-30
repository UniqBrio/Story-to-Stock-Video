#!/usr/bin/env python3
"""
preflight.py  (SVOS v2 render layer)
────────────
Checks everything a run needs BEFORE anything is fetched or rendered, so a
missing key, font or file stops the run up front instead of producing a
quietly broken video.

  Critical (exit 1) : ffmpeg/ffprobe or the drawtext filter missing ·
                      Tamil overlay text with no Tamil font, or an FFmpeg that cannot shape Tamil ·
                      stock shots still to fetch but no provider key ·
                      T4 product insert file missing · vo_path set but missing
  Critical (safety) : safety models / packages missing · on-screen text or VO line that fails the U-rated
                      blocklist · captions file missing
  Warnings          : brand font not found (fallback used) · music/BGM/sting/logo missing ·
                      Pillow missing (text auto-fit degrades) · some provider keys missing

Usage:
    python preflight.py project.json
    python preflight.py project.json --need-keys     # contact-sheet mode: every stock shot is searched
    python preflight.py project.json --no-fetch      # --skip-fetch runs: every stock shot needs its file
"""

import sys
import shutil
import argparse
import subprocess
from pathlib import Path

from svos_common import (load_project, banner, find_font, font_is_family, is_tamil, shot_kind, env_key,
                         ffmpeg_text_shaping)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROVIDERS = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY", "unsplash": "UNSPLASH_API_KEY"}


def has_drawtext() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
        return " drawtext " in r.stdout
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="Check keys, fonts, FFmpeg and referenced files before a run")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--need-keys", action="store_true",
                    help="Contact-sheet mode: every stock shot is searched; render-only gaps are warnings")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Phase 2 will be skipped: a stock shot without a local file is critical")
    args = ap.parse_args()

    project = load_project(Path(args.project))
    shots = project.get("shots", [])
    crit, warn = [], []
    render_crit = []          # only block a run that will render (not a contact-sheet search)

    # ── Tools ─────────────────────────────────────────────────────────────────
    missing_tools = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing_tools:
        crit.append(f"{' and '.join(missing_tools)} not on PATH — https://ffmpeg.org/download.html")
    elif not has_drawtext():
        crit.append("FFmpeg has no drawtext filter (needs libfreetype) — install a 'full' build, e.g. gyan.dev")
    try:
        import PIL  # noqa: F401
    except ImportError:
        warn.append("Pillow not installed — text auto-fit falls back to estimates (pip install pillow)")

    # ── Fonts ─────────────────────────────────────────────────────────────────
    tcfg = project.get("text", {})
    folder = tcfg.get("fonts_folder") or None
    brand = tcfg.get("font", "Inter") or "Inter"
    texts = [(s["shot_id"], (s.get("text_overlay") or "").strip()) for s in shots
             if (s.get("text_overlay") or "").strip() and shot_kind(s) != "product"]
    if any(not is_tamil(t) for _, t in texts):
        latin = find_font(brand, fonts_folder=folder)
        if not latin:
            render_crit.append("No Latin font found at all — overlays would use FFmpeg's built-in default")
        elif not font_is_family(latin, brand):
            warn.append(f"Brand font '{brand}' not found — overlays will use {Path(latin).name} "
                        f"(put {brand} TTFs in fonts/)")
    tamil_ids = [sid for sid, t in texts if is_tamil(t)]
    tamil_font = find_font("", tamil=True, fonts_folder=folder, tamil_preferred=tcfg.get("font_tamil", ""))
    if tamil_ids and not tamil_font:
        render_crit.append(f"Tamil text on {', '.join(tamil_ids)} but no Tamil font — glyphs would render as boxes "
                    f"(fonts/NotoSansTamil-Bold.ttf is bundled; check it is present)")
    if tamil_ids and tamil_font and not missing_tools and not ffmpeg_text_shaping(tamil_font):
        render_crit.append(f"Tamil text on {', '.join(tamil_ids)} but this FFmpeg cannot shape Tamil — vowel signs "
                           f"like ை would be drawn on the wrong side of the letter (misspelled on screen). "
                           f"Install a current full FFmpeg build with HarfBuzz (gyan.dev 'full' on Windows); FFmpeg 6.1 fails this check")

    # ── Shots / sources ───────────────────────────────────────────────────────
    to_fetch = []
    for s in shots:
        kind = shot_kind(s)
        lf = s.get("local_file", "")
        if kind == "product" and not (lf and Path(lf).exists()):
            render_crit.append(f"{s['shot_id']}: T4 product insert file missing — {lf or '(no Local File Path)'}")
        elif kind == "stock" and (args.need_keys or not (lf and Path(lf).exists())):
            to_fetch.append(s["shot_id"])
    api = project.get("api_keys", {})
    keys = {p: env_key(env, api.get(p, "")) for p, env in PROVIDERS.items()}
    no_key = [p for p, v in keys.items() if not v or "YOUR_" in v]
    if to_fetch and args.no_fetch:
        crit.append(f"Fetch is skipped but {len(to_fetch)} stock shot(s) have no source file: {', '.join(to_fetch)}")
    elif to_fetch:
        if len(no_key) == len(PROVIDERS):
            crit.append(f"{len(to_fetch)} stock shot(s) need fetching ({', '.join(to_fetch[:8])}"
                        f"{'…' if len(to_fetch) > 8 else ''}) but no provider key is set — "
                        f"set PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_API_KEY")
        elif no_key:
            warn.append(f"No API key for {', '.join(no_key)} — fewer candidates per shot")

    # ── Content safety (U-rated) — mandatory ─────────────────────────────────
    import content_safety as safety
    for issue in safety.readiness(project):
        crit.append(f"Content safety: {issue}")
    for s in shots:
        for field, label in (("text_overlay", "on-screen text"), ("vo_line", "VO line")):
            t = (s.get(field) or "").strip()
            if not t or (field == "text_overlay" and shot_kind(s) == "product"):
                continue
            st, hits = safety.check_text(t)
            if st == safety.FAIL and not safety.is_cleared(project, safety.text_key(t)):
                crit.append(f"{s['shot_id']}: {label} is not suitable for a U audience — {'; '.join(hits)}")
            elif st == safety.REVIEW and not safety.is_cleared(project, safety.text_key(t)):
                warn.append(f"{s['shot_id']}: {label} will need your content-safety approval — {'; '.join(hits)}")
    cap = project.get("safety", {}).get("captions_path", "")
    if cap and not Path(cap).exists():
        render_crit.append(f"captions_path set but file missing — {cap}")
    illu = [s["shot_id"] for s in shots if s.get("illustration_only")]
    if illu:
        warn.append(f"Cartoon / illustration only (swimwear-type subject): shots {', '.join(illu)} — real photos, "
                    f"real faces and celebrity likenesses will fail the safety check")

    # ── Audio / brand files ───────────────────────────────────────────────────
    audio = project.get("audio", {})
    vo = audio.get("vo_path", "")
    if vo and not Path(vo).exists():
        render_crit.append(f"vo_path set but file missing — {vo}")
    for k in ("music_path", "bgm_path", "sting_path"):
        p = audio.get(k, "")
        if p and not Path(p).exists():
            warn.append(f"{k} not found — {p} (layer will be skipped)")
    logo = project.get("logo", {}).get("path", "")
    if logo and not Path(logo).exists():
        warn.append(f"Logo not found — {logo} (logo card renders as a plain brand frame, no bug)")
    elif not logo and any(shot_kind(s) == "logo_card" for s in shots):
        warn.append("Logo card in the plan but no logo path set")

    if args.need_keys:
        warn += [f"{c}  (fix before rendering)" for c in render_crit]
    else:
        crit += render_crit

    # ── Report ────────────────────────────────────────────────────────────────
    banner("Preflight", f"{len(shots)} shots · {len(to_fetch)} to fetch · {len(texts)} overlay(s)",
           f"{len(crit)} critical · {len(warn)} warning(s)")
    for c in crit:
        print(f"  ❌  {c}")
    for w in warn:
        print(f"  ⚠️  {w}")
    if crit:
        print("\n  ❌  Preflight FAILED — fix the critical items above before rendering\n")
        sys.exit(1)
    print("\n  ✅  Preflight passed\n")


if __name__ == "__main__":
    main()
