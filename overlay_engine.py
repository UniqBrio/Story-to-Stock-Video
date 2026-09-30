#!/usr/bin/env python3
"""
overlay_engine.py  (SVOS v2 render layer)
─────────────────
Burns text overlays + (optionally) a corner logo bug onto assembled_raw.mp4 in
ONE FFmpeg pass, timed from the AUTHORITATIVE TIMELINE written by
transition_engine (so overlays no longer drift after dissolves).

Design rules implemented (text-on-screen-system · text-overlay-timing-director ·
tamil-text-overlay-typography · README §2):
  • Styles   : caption (subtitle-ish, white, shadow) · keyword (T3: bold uppercase
               anchor word) · card (T1: dark text on the card colour) · cta (T5)
  • Motion   : rise (fade + 40px ease-out rise, 0.35s in / 0.25s out) · fade · pop · none
  • Fit      : PIL-measured auto-wrap + shrink so nothing is ever clipped; ≤ 2 lines
               (3 on cards); 380px legibility floor (≥ 46px at 1080 wide)
  • Safe zones: Instagram header / caption+actions / right rail are never used
  • Tamil    : detected per line → Noto Sans Tamil → Nirmala UI; one drawtext per line
  • Logo     : end_only (default — the T5 card carries the logo) · bug · both · none;
               a bug never appears on cards, and per-shot Logo Bug yes/no overrides
  • Restraint: one element at a time — overlays on T4 product inserts are dropped

Usage:
    python overlay_engine.py project.json
    python overlay_engine.py project.json --force
"""

import sys
import shutil
import argparse
from pathlib import Path

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, probe_video_info,
                         compute_timeline, shot_kind, shot_treatment, find_font, ff_font_arg,
                         ff_escape_text, fit_text, measure_text, is_tamil, is_light, hex_to_ff,
                         hex_clean, safe_zones, words, banner, set_log, output_dir, BRAND)

STYLE = {
    #            size×   lines  upper  default pos      anim-in  anim-out
    "caption": {"mult": 0.90, "lines": 2, "upper": False, "pos": "bottom_center", "in": 0.30, "out": 0.25},
    "keyword": {"mult": 1.30, "lines": 2, "upper": True,  "pos": "center",        "in": 0.35, "out": 0.25},
    "card":    {"mult": 1.40, "lines": 3, "upper": False, "pos": "center",        "in": 0.45, "out": 0.30},
    "cta":     {"mult": 1.05, "lines": 2, "upper": False, "pos": "cta",           "in": 0.40, "out": 0.30},
}
MIN_LEGIBLE_PX_AT_1080 = 46      # ≈16px at the 380px legibility standard


def resolve_style(shot: dict) -> str:
    s = (shot.get("text_style") or "auto").lower()
    if s in STYLE:
        return s
    kind = shot_kind(shot)
    if kind == "card":
        return "card"
    if kind == "logo_card":
        return "cta"
    return "keyword" if words(shot.get("text_overlay", "")) <= 4 else "caption"


def block_position(position: str, style: str, block_h: int, tw: int, th: int, sz: dict) -> tuple[str, int]:
    """Return (x_expr for each line, top y of the text block)."""
    pad = 24
    if position == "cta":
        return "(w-text_w)/2", int(th * 0.60)
    if position == "top_center":
        return "(w-text_w)/2", sz["top"] + pad
    if position == "bottom_center":
        return "(w-text_w)/2", th - sz["bottom"] - block_h - pad
    if position == "lower_left":
        return str(sz["side"]), th - sz["bottom"] - block_h - pad
    if position == "upper_third":
        return "(w-text_w)/2", max(sz["top"] + pad, int(th * 0.30) - block_h // 2)
    if position == "lower_third":
        return "(w-text_w)/2", min(th - sz["bottom"] - block_h - pad, int(th * 0.66) - block_h // 2)
    return "(w-text_w)/2", (th - block_h) // 2          # center


def build_overlays(project: dict, timeline: list[dict]) -> tuple[list[str], list[dict], list[str]]:
    """Returns (drawtext filter strings, manifest rows, audit notes)."""
    tw, th = project.get("width", 1080), project.get("height", 1920)
    tcfg = project.get("text", {})
    base_size = int(tcfg.get("size_default", 72))
    use_shadow = bool(tcfg.get("shadow", True))
    box_mode = (tcfg.get("box") or "auto").lower()
    anim_default = (tcfg.get("anim_default") or "rise").lower()
    brand_color = hex_clean(tcfg.get("brand_color"), BRAND["orange"])
    card_text = hex_clean(tcfg.get("card_text"), BRAND["near_black"])
    sz = safe_zones(tw, th, project.get("platform", "reels")) if tcfg.get("safe_zones", True) else \
        {"top": 60, "bottom": 80, "side": 60, "right_rail": 0}
    font_latin = find_font(tcfg.get("font", "Inter"), fonts_folder=tcfg.get("fonts_folder") or None)
    font_tamil = find_font("", tamil=True, fonts_folder=tcfg.get("fonts_folder") or None,
                           tamil_preferred=tcfg.get("font_tamil", ""))
    tl_by_id = {t["shot_id"]: t for t in timeline}

    filters, manifest, audit = [], [], []
    for shot in project["shots"]:
        text = (shot.get("text_overlay") or "").strip()
        if not text:
            continue
        sid = shot["shot_id"]
        t = tl_by_id.get(sid)
        if not t:
            audit.append(f"{sid}: shot not in the assembled timeline — overlay skipped")
            continue
        kind = shot_kind(shot)
        if kind == "product":
            audit.append(f"{sid}: overlay '{text}' dropped — T4 product insert carries no text (README §2)")
            continue
        t_start = float(shot.get("text_start", 0.0))
        t_end = float(shot.get("text_end", 0.0))
        if t_end <= t_start:
            audit.append(f"{sid}: Text End ≤ Text Start — skipped")
            continue
        # never run into the next shot / transition
        limit = t["dur"] - (0.5 if t["trans_dur"] else 0.3) if kind not in ("card", "logo_card") else t["dur"]
        if t_end > limit + 0.001:
            t_end = max(t_start + 0.6, limit)
        abs_start = round(t["start"] + t_start, 3)
        abs_end = round(t["start"] + t_end, 3)

        style = resolve_style(shot)
        st = STYLE[style]
        anim = (shot.get("text_anim") or anim_default or "rise").lower()
        if anim not in ("rise", "fade", "pop", "none"):
            anim = "rise"
        tamil = is_tamil(text)
        font = font_tamil if tamil else font_latin
        if tamil and not font_tamil:
            audit.append(f"{sid}: Tamil text but no Tamil font found — glyphs will render as boxes. "
                         f"Drop NotoSansTamil-Bold.ttf into a fonts/ folder.")
        size = int(shot.get("text_size") or 0) or int(base_size * st["mult"])
        if tamil:
            size = int(size * 0.92)
        shown = text.upper() if st["upper"] and not tamil else text

        max_w = int(tw - 2 * sz["side"] - (sz["right_rail"] if style != "card" else 0))
        if style == "card":
            max_w = int(tw * 0.80)
        min_size = max(MIN_LEGIBLE_PX_AT_1080 * tw // 1080, 36)
        lines, size = fit_text(shown, font, size, max_w, max_lines=st["lines"], min_size=min_size)
        if not lines:
            continue
        if size < MIN_LEGIBLE_PX_AT_1080 * tw / 1080:
            audit.append(f"{sid}: text needed {size}px to fit — below the 380px legibility floor; shorten the line")

        line_gap = int(size * (0.42 if tamil else 0.32))
        line_h = max(measure_text(font, size, ln)[1] for ln in lines)
        block_h = len(lines) * line_h + (len(lines) - 1) * line_gap
        position = shot.get("text_position") or st["pos"]
        if style == "cta":
            position = "cta" if position in ("center", "bottom_center", "") else position
        x_expr, top_y = block_position(position, style, block_h, tw, th, sz)

        # Colour
        if style == "card":
            bg = hex_clean(shot.get("card_bg") or tcfg.get("card_bg"), BRAND["offwhite"])
            color = hex_clean(shot.get("text_color"), card_text if is_light(bg) else BRAND["white"])
        elif style == "cta":
            color = hex_clean(shot.get("text_color"), BRAND["white"])
        else:
            color = hex_clean(shot.get("text_color"), BRAND["white"])
        if color.upper() == brand_color.upper() and style == "keyword":
            pass   # brand orange anchor word — allowed (contrast-tested by the sheet author)

        # Motion
        a_in, a_out = st["in"], st["out"]
        if anim == "pop":
            a_in = 0.15
        if anim == "none":
            alpha_expr = "1"
        else:
            alpha_expr = (f"if(lt(t\\,{abs_start:.3f})\\,0\\,if(gt(t\\,{abs_end:.3f})\\,0\\,"
                          f"min(min(1\\,(t-{abs_start:.3f})/{a_in:.2f})\\,min(1\\,({abs_end:.3f}-t)/{a_out:.2f}))))")

        for i, ln in enumerate(lines):
            y0 = top_y + i * (line_h + line_gap)
            if anim == "rise":
                y_expr = f"{y0}+40*pow(1-min((t-{abs_start:.3f})/{a_in + 0.05:.2f}\\,1)\\,2)"
            else:
                y_expr = str(y0)
            parts = [f"drawtext=text='{ff_escape_text(ln)}'"]
            if font:
                parts.append(f"fontfile='{ff_font_arg(font)}'")
            parts += [f"fontsize={size}", f"fontcolor={hex_to_ff(color)}",
                      f"x={x_expr}", f"y='{y_expr}'", f"alpha='{alpha_expr}'",
                      f"enable='between(t\\,{abs_start:.3f}\\,{abs_end:.3f})'"]
            if style != "card":
                if use_shadow:
                    parts += ["shadowcolor=0x000000@0.55", "shadowx=3", "shadowy=4"]
                if style == "keyword":
                    parts += ["borderw=2", "bordercolor=0x000000@0.35"]
                if box_mode == "always" or (box_mode == "auto" and style == "caption" and not use_shadow):
                    parts += ["box=1", "boxcolor=0x000000@0.42", "boxborderw=18"]
            filters.append(":".join(parts))

        manifest.append({"shot_id": sid, "treatment": shot_treatment(shot), "style": style, "anim": anim,
                         "lines": lines, "font": Path(font).name if font else "ffmpeg-default",
                         "size": size, "color": color, "position": position,
                         "start": abs_start, "end": abs_end, "tamil": tamil})
    return filters, manifest, audit


def logo_windows(project: dict, timeline: list[dict]) -> list[tuple[float, float]]:
    """Time windows in which the corner logo bug is visible."""
    mode = (project.get("logo", {}).get("mode") or "end_only").lower()
    wins = []
    for t, shot in zip(timeline, [s for s in project["shots"] if s["shot_id"] in {x["shot_id"] for x in timeline}]):
        per = (shot.get("logo_bug") or "auto").lower()
        kind = t["kind"]
        if kind in ("card", "logo_card"):
            show = per == "yes"                       # never on cards unless forced
        elif per == "yes":
            show = True
        elif per == "no":
            show = False
        else:
            show = mode in ("bug", "both")
        if show:
            wins.append((t["start"], t["end"] - t["trans_dur"]))
    # merge adjacent windows
    merged: list[list[float]] = []
    for a, b in wins:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def restraint_audit(project: dict, manifest: list[dict]) -> list[str]:
    notes = []
    tcfg = project.get("text", {})
    kw = [m for m in manifest if m["style"] in ("keyword", "caption")]
    cards = [s for s in project["shots"] if shot_kind(s) == "card"]
    if len(kw) > int(tcfg.get("overlay_budget", 5)):
        notes.append(f"{len(kw)} stock overlays > budget {tcfg.get('overlay_budget', 5)} (README §2)")
    if len(cards) > int(tcfg.get("card_budget", 3)):
        notes.append(f"{len(cards)} blank text frames > budget {tcfg.get('card_budget', 3)}")
    for m in manifest:
        n = sum(words(ln) for ln in m["lines"])
        if m["style"] == "keyword" and n > 4:
            notes.append(f"{m['shot_id']}: keyword overlay is {n} words (≤ 4)")
        if m["style"] == "card" and n > 8:
            notes.append(f"{m['shot_id']}: card text is {n} words (≤ 8)")
        dur = m["end"] - m["start"]
        need = 1.5 if n <= 1 else 2.0 if n <= 4 else 2.5 if n <= 7 else 3.5
        if m["tamil"]:
            need += 0.5
        if dur < need - 0.05:
            notes.append(f"{m['shot_id']}: '{' '.join(m['lines'])}' shows {dur:.1f}s — reading-time minimum is {need:.1f}s")
    for a, b in zip(manifest, manifest[1:]):
        if b["start"] - a["end"] < 0.5 and b["start"] >= a["end"]:
            notes.append(f"{a['shot_id']}→{b['shot_id']}: only {b['start'] - a['end']:.2f}s between overlays (min 0.5s)")
    return notes


def main():
    ap = argparse.ArgumentParser(description="Burn text overlays and logo onto the assembled video")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    check_ffmpeg()
    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    assembled = project.get("assembled_file", "")
    if not assembled or not Path(assembled).exists():
        sys.exit("❌  assembled_file not found in project.json — run transition_engine.py first")
    out_file = Path(assembled).parent / "assembled_with_overlays.mp4"
    tw, th = project.get("width", 1080), project.get("height", 1920)

    # U-rated gate: every on-screen text and the logo must have passed the content-safety check
    import content_safety as safety
    blocked = [f"{s['shot_id']}: {safety.require_text(project, s['text_overlay'])}" for s in project["shots"]
               if (s.get("text_overlay") or "").strip() and shot_kind(s) != "product"
               and safety.require_text(project, s["text_overlay"])]
    lp = project.get("logo", {}).get("path", "")
    if lp and Path(lp).exists() and safety.require_media(project, lp):
        blocked.append(f"logo: {safety.require_media(project, lp)}")
    if blocked:
        print("  🛡   Not rendered — content not cleared:\n" + "\n".join(f"     • {b}" for b in blocked))
        sys.exit(1)

    timeline = project.get("timeline") or compute_timeline(project["shots"])
    filters, manifest, audit = build_overlays(project, timeline)
    audit += restraint_audit(project, manifest)

    logo_cfg = project.get("logo", {})
    logo_path = logo_cfg.get("path", "")
    wins = logo_windows(project, timeline) if logo_path and Path(logo_path).exists() else []
    if logo_path and not Path(logo_path).exists() and (logo_cfg.get("mode") in ("bug", "both")):
        audit.append(f"logo file missing: {logo_path}")

    banner("Overlay Engine",
           f"{len(manifest)} overlay(s) · logo mode: {logo_cfg.get('mode', 'end_only')} "
           f"· bug windows: {len(wins)}",
           f"fonts: {', '.join(sorted({m['font'] for m in manifest})) or '—'}")

    if out_file.exists() and not args.force:
        print("  ⏭️  assembled_with_overlays.mp4 exists — use --force to rebuild")
        project["overlaid_file"] = str(out_file)
        project["overlay_manifest"] = manifest
        save_project(project, proj_path)
        return

    if not filters and not wins:
        print("  ℹ️  No overlays and no logo bug — copying the assembled file")
        shutil.copy2(assembled, out_file)
    else:
        graph = []
        cur = "[0:v]"
        if filters:
            graph.append(f"{cur}{','.join(filters)}[txt]")
            cur = "[txt]"
        inputs = ["-i", assembled]
        if wins:
            logo_w = int(tw * float(logo_cfg.get("scale", 0.12)))
            pad = int(tw * 0.045)
            sz = safe_zones(tw, th, project.get("platform", "reels"))
            pos = {
                "top_left":     (pad, sz["top"] + pad // 2),
                "top_right":    (f"W-w-{pad}", sz["top"] + pad // 2),
                "bottom_left":  (pad, f"H-h-{sz['bottom'] + pad // 2}"),
                "bottom_right": (f"W-w-{pad}", f"H-h-{sz['bottom'] + pad // 2}"),
                "center":       ("(W-w)/2", "(H-h)/2"),
            }.get(logo_cfg.get("position", "top_right"), (f"W-w-{pad}", sz["top"] + pad // 2))
            enable = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in wins)
            inputs += ["-i", logo_path]
            graph.append(f"[1:v]format=rgba,scale={logo_w}:-1:flags=lanczos,"
                         f"colorchannelmixer=aa={float(logo_cfg.get('opacity', 0.9)):.2f}[logo]")
            graph.append(f"{cur}[logo]overlay=x={pos[0]}:y={pos[1]}:enable='{enable}':format=auto[vout]")
        else:
            graph[-1] = graph[-1].replace("[txt]", "[vout]")
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "0:a?",
               "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
               "-c:a", "copy", "-movflags", "+faststart", str(out_file)]
        print("  Burning overlays" + (" + logo bug" if wins else "") + " (single pass)...")
        if not run_ff(cmd, "overlays"):
            sys.exit(1)

    for m in manifest:
        print(f"    {m['shot_id']} {m['treatment']} {m['style']:<7} {m['start']:6.2f}→{m['end']:6.2f}s "
              f"{m['size']}px {m['anim']:<4} | " + " / ".join(m["lines"]))
    if audit:
        print("\n  ⚠️  Overlay audit:")
        for a in audit:
            print(f"     • {a}")

    dur = probe_video_info(out_file)["duration"]
    project["overlaid_file"] = str(out_file)
    project["overlay_manifest"] = manifest
    project["overlay_audit"] = audit
    save_project(project, proj_path)
    print(f"\n  ✅  assembled_with_overlays.mp4  ({dur:.2f}s)\n")


if __name__ == "__main__":
    main()
