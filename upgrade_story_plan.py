#!/usr/bin/env python3
"""
upgrade_story_plan.py  (SVOS v2)
─────────────────────
Brings an existing story_plan.xlsx up to the v2 render layer WITHOUT touching
any value you already entered:

  Shot Plan        + columns: Treatment · Text Style · Text Anim · Text Color · Card BG ·
                     Logo Bug · Font Size · VO Line · Notes   (each with a drop-down)
                   + drop-downs on the existing enum columns
                   + Treatment pre-filled from the scene (e.g. the "Logo card" row → T5)
  Project Settings + rows for every new design/audio setting, with notes
  Backup           story_plan.backup-<timestamp>.xlsx is written first

Usage:
    python upgrade_story_plan.py story_plan.xlsx
"""

import sys
import shutil
import argparse
from copy import copy
from pathlib import Path
from datetime import datetime

try:
    import openpyxl
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("❌  Run: pip install openpyxl")

from svos_common import shot_kind, BRAND

NEW_COLUMNS = [
    # header, width, default, note, validation list
    ("Treatment",  11, "",     "T1 blank text frame · T2 clean stock · T3 stock+keyword · T4 product screen · T5 logo/CTA card", "T1,T2,T3,T4,T5"),
    ("Text Style", 11, "auto", "auto · caption · keyword · card · cta", "auto,caption,keyword,card,cta"),
    ("Text Anim",  10, "",     "rise (default) · fade · pop · none", "rise,fade,pop,none"),
    ("Text Color", 11, "",     "#RRGGBB — blank = style default (white; near-black on light cards)", None),
    ("Card BG",    11, "",     "#RRGGBB for T1/T5 cards — blank = card_bg_default / logo_card_bg", None),
    ("Logo Bug",   9,  "auto", "auto (follow logo_mode) · yes · no", "auto,yes,no"),
    ("Font Size",  9,  "",     "px override — blank = style default", None),
    ("VO Line",    40, "",     "The spoken line this shot covers (from the VO Timing Map)", None),
    ("Notes",      30, "",     "Why this treatment was earned / G3 comments", None),
]

EXISTING_VALIDATIONS = {
    "asset priority": "video_first,image_first,video_only,image_only",
    "color grade":    "chaos,pivot,cta,none",
    "ken burns":      "zoom_in,zoom_out,pan_left,pan_right,none",
    "text position":  "center,lower_left,bottom_center,top_center,upper_third,lower_third",
    "transition out": "cut,dissolve,fade_black,fade_white,none",
}

NEW_SETTINGS = [
    ("brand_color",       BRAND["orange"],  "Brio Orange — anchor words / CTAs"),
    ("card_bg_default",   BRAND["offwhite"], "T1 card background (warm off-white). Charcoal #1E1E22 for grave lines"),
    ("card_text_color",   BRAND["near_black"], "T1 card text on light cards"),
    ("logo_card_bg",      BRAND["purple"],  "T5 end card background (Brio Purple)"),
    ("logo_card_scale",   "0.42",           "Logo width on the end card as a fraction of video width"),
    ("logo_mode",         "end_only",       "end_only (default — README §2b) · bug (corner logo throughout) · both · none"),
    ("fonts_folder",      "",               "Folder with brand fonts (Inter-Bold.ttf, NotoSansTamil-Bold.ttf). Blank = Windows fonts"),
    ("text_font_tamil",   "",               "Tamil font file/name — blank = Noto Sans Tamil → Nirmala UI"),
    ("text_anim_default", "rise",           "rise · fade · pop · none"),
    ("text_box",          "auto",           "auto · always · never — semi-transparent plate behind captions"),
    ("unified_grade",     "warm_soft",      "ONE finishing grade on all stock: warm_soft · clean_neutral · cool_calm · none"),
    ("film_grain",        "false",          "true adds fine temporal grain to stock (off for screen recordings)"),
    ("platform",          "reels",          "reels · shorts · landscape — sets the UI safe zones"),
    ("safe_zones",        "true",           "Keep text out of Instagram's header / caption / right rail"),
    ("target_lufs",       "-14",            "Integrated loudness target (Instagram / sonic-brand-identity: −14)"),
    ("true_peak",         "-1.5",           "True-peak ceiling in dBTP"),
    ("duck_db",           "8",              "How far music drops under speech (dB)"),
    ("sting_path",        "",               "End sting / audio logo (0.5–1.5s) — lands 0.4s before the end"),
    ("sting_volume",      "0.9",            "0.0–1.0"),
    ("vo_start",          "0.0",            "Seconds to delay the VO from the first frame"),
    ("overlay_budget",    "5",              "Max stock overlays (T3) — README §2"),
    ("card_budget",       "3",              "Max blank text frames (T1) — README §2"),
    ("cover_time",        "",               "Seconds for the cover frame — blank = first T1 card"),
    ("max_duration",      "90",             "Warn if the assembled video is longer than this"),
]


def norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


def main():
    ap = argparse.ArgumentParser(description="Upgrade story_plan.xlsx to the SVOS v2 columns/settings")
    ap.add_argument("xlsx")
    args = ap.parse_args()
    path = Path(args.xlsx)
    if not path.exists():
        sys.exit(f"❌  Not found: {path}")

    backup = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    shutil.copy2(path, backup)
    wb = openpyxl.load_workbook(path)
    changes = []

    # ── Shot Plan ─────────────────────────────────────────────────────────────
    ws = wb["Shot Plan"]
    header_row = next((r for r in range(1, 7) if norm(ws.cell(row=r, column=1).value) == "shot id"), 3)
    headers = {norm(ws.cell(row=header_row, column=c).value): c for c in range(1, ws.max_column + 1)
               if ws.cell(row=header_row, column=c).value}
    last_data_row = max((r for r in range(header_row + 1, ws.max_row + 1) if ws.cell(row=r, column=1).value), default=header_row)
    template_hdr = ws.cell(row=header_row, column=1)
    template_grp = ws.cell(row=header_row - 1, column=1) if header_row > 1 else None
    template_cell = ws.cell(row=header_row + 1, column=2)

    def add_validation(col_idx: int, options: str):
        dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True, showErrorMessage=False)
        ws.add_data_validation(dv)
        letter = get_column_letter(col_idx)
        dv.add(f"{letter}{header_row + 1}:{letter}{max(last_data_row, header_row + 1) + 200}")

    for hdr, opts in EXISTING_VALIDATIONS.items():
        if hdr in headers:
            add_validation(headers[hdr], opts)

    next_col = max(headers.values()) + 1
    first_new = None
    for header, width, default, note, options in NEW_COLUMNS:
        if norm(header) in headers:
            continue
        c = next_col
        first_new = first_new or c
        cell = ws.cell(row=header_row, column=c, value=header)
        cell.font, cell.fill, cell.alignment, cell.border = (copy(template_hdr.font), copy(template_hdr.fill),
                                                             copy(template_hdr.alignment), copy(template_hdr.border))
        ws.column_dimensions[get_column_letter(c)].width = width
        for r in range(header_row + 1, last_data_row + 1):
            v = default
            if header == "Treatment":
                shot = {"scene_desc": ws.cell(row=r, column=headers.get("scene description", 2)).value or "",
                        "asset_type": ws.cell(row=r, column=headers.get("asset type", 6)).value or "",
                        "text_overlay": ws.cell(row=r, column=headers.get("text overlay", 13)).value or ""}
                k = shot_kind(shot)
                v = {"card": "T1", "logo_card": "T5", "product": "T4"}.get(k, "T3" if shot["text_overlay"] else "T2")
            dc = ws.cell(row=r, column=c, value=v if v != "" else None)
            dc.font, dc.alignment, dc.border = copy(template_cell.font), copy(template_cell.alignment), copy(template_cell.border)
        if options:
            add_validation(c, options)
        hc = ws.cell(row=header_row, column=c)
        hc.comment = None
        from openpyxl.comments import Comment
        hc.comment = Comment(note, "SVOS v2")
        headers[norm(header)] = c
        changes.append(f"Shot Plan: + column '{header}'")
        next_col += 1
    if first_new and template_grp is not None and header_row > 1:
        g = ws.cell(row=header_row - 1, column=first_new, value="TREATMENT & DESIGN (v2)")
        g.font, g.fill, g.alignment = copy(template_grp.font), copy(template_grp.fill), copy(template_grp.alignment)

    # ── Project Settings ──────────────────────────────────────────────────────
    ps = wb["Project Settings"]
    existing = {norm(ps.cell(row=r, column=1).value) for r in range(1, ps.max_row + 1)}
    tmpl_row = next((r for r in range(3, ps.max_row + 1) if ps.cell(row=r, column=1).value), 3)
    row = ps.max_row + 1
    added_any = False
    for key, default, note in NEW_SETTINGS:
        if key in existing:
            continue
        if not added_any:
            ps.cell(row=row, column=1, value="— v2 design & audio settings —")
            row += 1
            added_any = True
        for ci, val in enumerate((key, default, note), start=1):
            cell = ps.cell(row=row, column=ci, value=val)
            src = ps.cell(row=tmpl_row, column=ci)
            cell.font, cell.alignment, cell.border, cell.fill = copy(src.font), copy(src.alignment), copy(src.border), copy(src.fill)
        changes.append(f"Project Settings: + {key} = {default or '(blank)'}")
        row += 1

    wb.save(path)
    print(f"\n  ✅  Upgraded {path.name}   (backup: {backup.name})")
    for c in changes:
        print(f"     • {c}")
    if not changes:
        print("     • nothing to add — already v2")
    print()


if __name__ == "__main__":
    main()
