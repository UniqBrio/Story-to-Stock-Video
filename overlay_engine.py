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
  • Tamil    : detected per line → Noto Sans Tamil → Nirmala UI; drawn by libass (`ass` filter),
               because drawtext never reorders pre-base vowel signs (கை would read க + ை)
  • CTA      : text colour checked against the card (WCAG 3:1) and switched if it would not read;
               cta_style = button → a rounded #B85F00 pill with white text and a soft pop-in (libass)
  • Logo     : end_only (default — the T5 card carries the logo) · bug · both · none;
               the bug uses the logo variant (same folder) that stays visible on the footage behind it;
               a bug never appears on cards, and per-shot Logo Bug yes/no overrides
  • Restraint: one element at a time — overlays on T4 product inserts are dropped

Usage:
    python overlay_engine.py project.json
    python overlay_engine.py project.json --force
"""

import sys
import shutil
import argparse
import subprocess
from pathlib import Path

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, probe_video_info,
                         compute_timeline, shot_kind, shot_treatment, find_font, ff_font_arg,
                         ff_escape_text, fit_text, measure_text, is_tamil, is_light, hex_to_ff,
                         hex_clean, safe_zones, words, banner, set_log, output_dir, BRAND,
                         font_family, ass_color, ass_escape, ass_document, ass_time, ass_filter,
                         ass_size_scale, stage_ass_fonts, best_logo, contrast_ratio, readable_color,
                         CTA_BUTTON, TEXT_MIN_CONTRAST, variant_suffix)

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


def build_overlays(project: dict, timeline: list[dict],
                   ass_dir: Path | None = None) -> tuple[list[str], list[dict], list[str]]:
    """Returns (filter strings, manifest rows, audit notes). Latin lines → drawtext; Tamil lines →
    one tamil_overlays.ass in ass_dir, burned by a trailing `ass=` filter."""
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
    cta_style = (tcfg.get("cta_style") or "text").lower()
    cta_pill = hex_clean(tcfg.get("cta_color"), CTA_BUTTON)
    logo_bg = hex_clean(project.get("logo", {}).get("card_bg"), BRAND["purple"])

    filters, manifest, audit = [], [], []
    ass_events, ass_fonts = [], set()
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
            card_bg = hex_clean(shot.get("card_bg") or logo_bg, BRAND["purple"]) if kind == "logo_card" else ""
            if card_bg and cta_style != "button" and contrast_ratio(color, card_bg) < TEXT_MIN_CONTRAST:
                fixed = readable_color(card_bg, color)
                audit.append(f"{sid}: CTA colour {color} does not read on {card_bg} "
                             f"({contrast_ratio(color, card_bg):.1f}:1) — using {fixed}")
                color = fixed
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

        button = style == "cta" and cta_style == "button" and bool(font)
        if button:
            bg = hex_clean(shot.get("card_bg") or logo_bg, BRAND["purple"]) if kind == "logo_card" else ""
            color = readable_color(cta_pill, BRAND["white"])
            ass_events += _ass_button(lines, font, size, color, cta_pill, bg, a_in, a_out, abs_start, abs_end,
                                      top_y, line_h, line_gap, tw)
            ass_fonts.add(font)
            lines_drawn = True
        elif tamil and font:
            ass_events += _ass_lines(lines, font, size, color, style, anim, a_in, a_out, abs_start, abs_end,
                                     x_expr, top_y, line_h, line_gap, tw, use_shadow, box_mode)
            ass_fonts.add(font)
            lines_drawn = True
        else:
            lines_drawn = False
        for i, ln in enumerate(lines):
            if lines_drawn:
                break
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
                         "start": abs_start, "end": abs_end, "tamil": tamil, "button": button,
                         "renderer": "libass" if lines_drawn else "drawtext"})
    # early CTA chip: small pill in the lower third of the shot before the end card (slot set by vo_aligner)
    for shot in project["shots"]:
        chip, t = shot.get("cta_chip"), tl_by_id.get(shot["shot_id"])
        if not chip or not t or not font_latin:
            continue
        csize = max(MIN_LEGIBLE_PX_AT_1080 * tw // 1080, int(base_size * 0.75))
        lh = measure_text(font_latin, csize, chip["text"])[1]
        _, top = block_position("lower_third", "keyword", lh, tw, th, sz)
        a0 = round(t["start"] + float(chip["start"]), 3)
        a1 = round(min(t["start"] + float(chip["end"]), t["end"] - (0.5 if t["trans_dur"] else 0.3)), 3)
        ass_events += _ass_button([chip["text"]], font_latin, csize, readable_color(cta_pill, BRAND["white"]),
                                  cta_pill, "", 0.25, 0.25, a0, a1, top, lh, 0, tw)
        ass_fonts.add(font_latin)
        manifest.append({"shot_id": shot["shot_id"], "treatment": shot_treatment(shot), "style": "chip", "anim": "pop",
                         "lines": [chip["text"]], "font": Path(font_latin).name, "size": csize, "color": BRAND["white"],
                         "position": "lower_third", "start": a0, "end": a1, "tamil": False, "button": True,
                         "chip": True, "renderer": "libass"})
    if ass_events:
        ass_dir = Path(ass_dir or ".")
        ass_path = ass_dir / f"libass_overlays{variant_suffix(project)}.ass"
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(ass_document(tw, th, ass_events), encoding="utf-8")
        fonts_dir = ass_dir / "ass_fonts"
        for f in sorted(ass_fonts):
            stage_ass_fonts(f, fonts_dir)
        filters.append(ass_filter(ass_path, fonts_dir))
    return filters, manifest, audit


def _pill_path(w: float, h: float) -> str:
    """ASS vector drawing of a rounded rectangle (fully rounded ends), origin top-left."""
    r = h / 2.0
    k = 0.5523 * r                                           # cubic-Bézier quarter-circle constant
    pts = [f"m {r:.0f} 0", f"l {w - r:.0f} 0", f"b {w - r + k:.0f} 0 {w:.0f} {r - k:.0f} {w:.0f} {r:.0f}",
           f"b {w:.0f} {r + k:.0f} {w - r + k:.0f} {h:.0f} {w - r:.0f} {h:.0f}", f"l {r:.0f} {h:.0f}",
           f"b {r - k:.0f} {h:.0f} 0 {r + k:.0f} 0 {r:.0f}", f"b 0 {r - k:.0f} {r - k:.0f} 0 {r:.0f} 0"]
    return " ".join(pts)


def _ass_button(lines, font, size, text_hex, pill_hex, bg_hex, a_in, a_out, start, end,
                top_y, line_h, line_gap, tw) -> list[str]:
    """CTA as a button: a rounded pill behind the text, both popping in (80 → 106 → 100 %) and fading out.
    The pill gets a thin outline when it would not stand out from the card behind it."""
    fs = size * ass_size_scale(font)
    widths = [measure_text(font, size, ln)[0] for ln in lines]
    block_h = len(lines) * line_h + (len(lines) - 1) * line_gap
    pad_x, pad_y = int(size * 0.65), int(size * 0.38)
    w, h = max(widths) + 2 * pad_x, block_h + 2 * pad_y
    cx, cy = tw // 2, int(top_y + block_h / 2)
    pop = "\\fscx80\\fscy80\\t(0,220,\\fscx106\\fscy106)\\t(220,380,\\fscx100\\fscy100)"
    fade = f"\\fad({int(min(a_in, 0.25) * 1000)},{int(a_out * 1000)})"
    pc, _ = ass_color(pill_hex)
    sc, sa = ass_color("#000000", 0.45)
    outline = ""
    if bg_hex and contrast_ratio(pill_hex, bg_hex) < 3.0:
        oc, _ = ass_color(readable_color(bg_hex, BRAND["white"]))
        outline = f"\\bord4\\3c{oc}"
    out = [f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Base,,0,0,0,,"
           f"{{\\an5\\pos({cx},{cy})\\p1\\1c{pc}{outline or chr(92) + 'bord0'}\\xshad0\\yshad6\\4c{sc}\\4a{sa}"
           f"{pop}{fade}}}{_pill_path(w, h)}"]
    tc, _ = ass_color(text_hex)
    for i, ln in enumerate(lines):
        y = int(top_y + i * (line_h + line_gap) + line_h / 2)
        out.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Base,,0,0,0,,"
                   f"{{\\an5\\pos({cx},{y})\\fn{font_family(font)}\\fs{fs:.1f}\\b1\\1c{tc}\\bord0\\shad0"
                   f"{pop}{fade}}}{ass_escape(ln)}")
    return out


def _ass_lines(lines, font, size, color, style, anim, a_in, a_out, start, end,
               x_expr, top_y, line_h, line_gap, tw, use_shadow, box_mode) -> list[str]:
    """ASS Dialogue lines that mirror the drawtext styling: same size, colour, position,
    shadow / border / plate, and the same fade · rise · pop motion."""
    fs = size * ass_size_scale(font)
    centred = "text_w" in str(x_expr)
    x = tw // 2 if centred else int(str(x_expr))
    c, _ = ass_color(color)
    tags = [f"\\an{8 if centred else 7}", f"\\fn{font_family(font)}", f"\\fs{fs:.1f}", "\\b1", f"\\1c{c}"]
    box = style != "card" and (box_mode == "always" or (box_mode == "auto" and style == "caption" and not use_shadow))
    if style == "card" or box:
        tags += ["\\shad0"] + ([] if box else ["\\bord0"])
    else:
        tags += ["\\shad0"]
        if use_shadow:
            sc, sa = ass_color("#000000", 0.55)
            tags += ["\\xshad3", "\\yshad4", f"\\4c{sc}", f"\\4a{sa}"]
        if style == "keyword":
            bc, ba = ass_color("#000000", 0.35)
            tags += ["\\bord2", f"\\3c{bc}", f"\\3a{ba}"]
        else:
            tags += ["\\bord0"]
    if anim != "none":
        tags.append(f"\\fad({int(a_in * 1000)},{int(a_out * 1000)})")
    out = []
    for i, ln in enumerate(lines):
        y0 = top_y + i * (line_h + line_gap)
        if anim == "rise":
            place = f"\\move({x},{y0 + 40},{x},{y0},0,{int((a_in + 0.05) * 1000)})"
        else:
            place = f"\\pos({x},{y0})"
        out.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{'Box' if box else 'Base'},,0,0,0,,"
                   f"{{{''.join(tags)}{place}}}{ass_escape(ln)}")
    return out


def bug_box(position: str, tw: int, th: int, sz: dict, logo_w: int) -> tuple[int, int, int, int]:
    """Approximate pixel box (x, y, w, h) the corner bug covers — the footage it must stand out from."""
    pad, h = int(tw * 0.045), max(8, logo_w // 3)
    x = {"top_left": pad, "bottom_left": pad, "center": (tw - logo_w) // 2}.get(position, tw - logo_w - pad)
    y = {"bottom_left": th - sz["bottom"] - pad // 2 - h, "bottom_right": th - sz["bottom"] - pad // 2 - h,
         "center": (th - h) // 2}.get(position, sz["top"] + pad // 2)
    return x, y, logo_w, h


def bug_backgrounds(video: str, wins: list, box: tuple) -> list[tuple[int, int, int]]:
    """Colours of the footage behind the bug: an 8×3 grid from up to 9 frames across its windows."""
    x, y, w, h = box
    times = []
    for a, b in wins:
        times += [a + (b - a) * f for f in (0.2, 0.5, 0.8)]
    out = []
    for t in times[:9]:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"crop={w}:{h}:{x}:{y},scale=8:3:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, timeout=60)
        px = r.stdout
        out += [tuple(px[i:i + 3]) for i in range(0, len(px) - 2, 3)]
    return out


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
    out_file = Path(assembled).parent / f"assembled_with_overlays{variant_suffix(project)}.mp4"
    tw, th = project.get("width", 1080), project.get("height", 1920)

    # U-rated gate: every on-screen text and the logo must have passed the content-safety check
    import content_safety as safety
    blocked = [f"{s['shot_id']}: {safety.require_text(project, s['text_overlay'])}" for s in project["shots"]
               if (s.get("text_overlay") or "").strip() and shot_kind(s) != "product"
               and safety.require_text(project, s["text_overlay"])]
    blocked += [f"{s['shot_id']} CTA chip: {safety.require_text(project, s['cta_chip']['text'])}"
                for s in project["shots"] if s.get("cta_chip") and safety.require_text(project, s["cta_chip"]["text"])]
    timeline = project.get("timeline") or compute_timeline(project["shots"])
    logo_cfg = project.get("logo", {})
    logo_path = logo_cfg.get("path", "")
    wins = logo_windows(project, timeline) if logo_path and Path(logo_path).exists() else []
    logo_note = ""
    if wins:      # corner bug: pick the variant that stays visible on the footage it sits over
        logo_w = int(tw * float(logo_cfg.get("scale", 0.12)))
        sz0 = safe_zones(tw, th, project.get("platform", "reels"))
        bgs = bug_backgrounds(assembled, wins, bug_box(logo_cfg.get("position", "top_right"), tw, th, sz0, logo_w))
        pick, logo_note = best_logo(logo_path, bgs)
        if pick != logo_path and not safety.require_media(project, pick):
            logo_path = pick
        elif pick != logo_path:
            logo_note += " — that variant has not passed the safety check, keeping the original"
    if logo_path and Path(logo_path).exists() and wins and safety.require_media(project, logo_path):
        blocked.append(f"logo: {safety.require_media(project, logo_path)}")
    if blocked:
        print("  🛡   Not rendered — content not cleared:\n" + "\n".join(f"     • {b}" for b in blocked))
        sys.exit(1)

    filters, manifest, audit = build_overlays(project, timeline, ass_dir=out_file.parent)
    audit += restraint_audit(project, manifest)

    if logo_note:
        audit.append(f"logo bug: {logo_note}")
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
