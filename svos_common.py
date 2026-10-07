#!/usr/bin/env python3
"""
svos_common.py
──────────────
Shared helpers for every render-layer script (SVOS v2 render layer).

    • console UTF-8 setup            • ffmpeg / ffprobe wrappers + run log
    • project.json load / save        • brand palette + safe zones
    • font resolution (Latin + Tamil) • text measurement / auto-fit (PIL)
    • drawtext escaping               • timeline maths (transition overlaps)
    • libass (.ass) text for Tamil    — drawtext cannot shape it
    • shot "kind" resolution          (T1 card · T2/T3 stock · T4 product · T5 logo card)

Import from any stage:   from svos_common import *
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Console ───────────────────────────────────────────────────────────────────
def setup_console() -> None:
    """Force UTF-8 so ✅/❌ banners never crash on a cp1252 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

setup_console()

# ── Brand + layout constants ─────────────────────────────────────────────────
BRAND = {
    "orange":     "#DE7D14",   # Brio Orange — CTAs, anchor words, THE single most important frame
    "purple":     "#6708C0",   # Brio Purple — titles, premium establishing moments, end card bg
    "offwhite":   "#FAF7F2",   # default T1 card background
    "charcoal":   "#1E1E22",   # grave / dark-footage T1 card background
    "near_black": "#111114",   # card text on light cards
    "white":      "#FFFFFF",
}

# Treatment codes from the SVOS README §1 → render "kind"
TREATMENT_KIND = {
    "T1": "card",        # blank text frame
    "T2": "stock",       # clean stock
    "T3": "stock",       # stock + keyword overlay (overlay style = keyword)
    "T4": "product",     # app screenshot / screen recording insert
    "T5": "logo_card",   # logo / CTA card
}

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

def safe_zones(tw: int, th: int, platform: str = "reels") -> dict:
    """
    Platform UI-safe margins in pixels. Instagram Reels covers roughly the top
    12–14% (header) and bottom 20–22% (caption, audio, actions) plus a right-hand
    rail (~13% width). Text must never sit there.
    """
    platform = (platform or "reels").lower()
    if platform in ("reels", "instagram", "shorts", "tiktok"):
        return {"top": int(th * 0.135), "bottom": int(th * 0.22),
                "side": int(tw * 0.06),  "right_rail": int(tw * 0.13)}
    if platform in ("landscape", "youtube", "16:9"):
        return {"top": int(th * 0.08), "bottom": int(th * 0.12),
                "side": int(tw * 0.05), "right_rail": 0}
    return {"top": int(th * 0.10), "bottom": int(th * 0.15),
            "side": int(tw * 0.05), "right_rail": 0}

# ── project.json ─────────────────────────────────────────────────────────────
def load_project(path: Path | str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"❌  Not found: {p}")
    return json.loads(p.read_text(encoding="utf-8-sig"))

def save_project(project: dict, path: Path | str) -> None:
    Path(path).write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")

def project_dir(project: dict, proj_path: Path) -> Path:
    src = project.get("source_xlsx", "")
    return Path(src).parent if src else proj_path.parent

def assets_dir(project: dict, proj_path: Path) -> Path:
    return Path(project.get("assets_folder") or (proj_path.parent / "Assets"))

def output_dir(project: dict, proj_path: Path) -> Path:
    return Path(project.get("output_folder") or (proj_path.parent / "Output"))

# ── ffmpeg wrappers ──────────────────────────────────────────────────────────
def check_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        sys.exit("❌  " + " and ".join(missing) + " not found.\n"
                 "    Download https://ffmpeg.org/download.html and add ffmpeg/bin to PATH")

_SHAPING: dict = {}
_ASS_SCALE: dict = {}

# ── Complex-script text via libass ───────────────────────────────────────────
# FFmpeg's drawtext hands HarfBuzz a Latin script hint, so Tamil pre-base vowel
# signs (ெ ே ை ொ ோ ௌ) are never reordered: 'கை' comes out as 'க' + 'ை' — misspelled
# on screen with any font, any FFmpeg version. libass (FFmpeg's `ass` filter)
# detects the script per run and shapes Tamil correctly, so Tamil overlays are
# written as an .ass file and burned with `ass=`; Latin text stays on drawtext.

def font_family(font_path: str) -> str:
    """Family name libass matches on, read from the font file ('Noto Sans Tamil')."""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(font_path, 20).getname()[0]
    except Exception:
        return Path(font_path).stem.split("-")[0]

def ass_color(hex_color: str, opacity: float = 1.0) -> tuple[str, str]:
    """'#RRGGBB', opacity → ('&HBBGGRR&', '&HAA&') for ASS colour / alpha override tags."""
    h = hex_clean(hex_color).lstrip("#")
    a = max(0, min(255, round(255 * (1.0 - opacity))))
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper(), f"&H{a:02X}&"

def ass_escape(text: str) -> str:
    """Plain text for an ASS Dialogue line: braces start override blocks, backslash starts escapes."""
    return text.replace("\\", "\uFF3C").replace("{", "(").replace("}", ")").replace("\n", " ")

def ass_document(play_w: int, play_h: int, events: list[str]) -> str:
    """A complete .ass file. PlayRes = frame size, so ASS units are pixels. No wrapping —
    lines are already fitted by fit_text(). 'Box' style = BorderStyle 3 (opaque plate)."""
    fmt = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
           "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
           "MarginL, MarginR, MarginV, Encoding")
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {play_w}", f"PlayResY: {play_h}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", fmt,
        "Style: Base,Arial,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "Style: Box,Arial,72,&H00FFFFFF,&H00FFFFFF,&H6B000000,&H6B000000,-1,0,0,0,100,100,0,0,3,18,0,7,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events, ""])

def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

def stage_ass_fonts(font_path: str, dest_dir: Path) -> Path:
    """Copy one font into its own folder for `ass=fontsdir=` — pointing libass at C:/Windows/Fonts
    would make it load every installed font on each render."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(font_path).name
    if not target.exists() or target.stat().st_size != Path(font_path).stat().st_size:
        shutil.copy2(font_path, target)
    return dest_dir

def ass_filter(ass_path: Path, fonts_dir: Path) -> str:
    return f"ass=filename='{ff_font_arg(str(ass_path))}':fontsdir='{ff_font_arg(str(fonts_dir))}'"

def _render_ass_gray(font_path: str, text: str, fs: float, W: int, H: int, ffmpeg: str = "ffmpeg") -> bytes:
    """One white-on-black grey frame of `text` drawn by libass at the top-left (10, 10)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fonts = stage_ass_fonts(font_path, d / "fonts")
        ev = (f"Dialogue: 0,{ass_time(0)},{ass_time(5)},Base,,0,0,0,,"
              f"{{\\an7\\pos(10,10)\\fn{font_family(font_path)}\\fs{fs:.1f}\\bord0\\shad0}}{ass_escape(text)}")
        (d / "probe.ass").write_text(ass_document(W, H, [ev]), encoding="utf-8")
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", f"color=black:s={W}x{H}", "-frames:v", "1",
                            "-vf", ass_filter(d / "probe.ass", fonts), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=60)
    return r.stdout if r.returncode == 0 and len(r.stdout) == W * H else b""

def ass_size_scale(font_path: str, ffmpeg: str = "ffmpeg") -> float:
    """ASS font size per drawtext/PIL pixel size. libass sizes a font by its ascender+descender,
    PIL and drawtext by the em, so the same number draws smaller in libass. Measured once per
    font by comparing the ink height of 'க' (Tamil fonts) or 'H' (Latin fonts) at size 100."""
    key = (ffmpeg, font_path)
    if key in _ASS_SCALE:
        return _ASS_SCALE[key]
    scale = 1.0
    try:
        from PIL import ImageFont
        probe = "\u0B95" if is_tamil_font(font_path) else "H"
        box = ImageFont.truetype(font_path, 100).getbbox(probe)
        pil_h = box[3] - box[1]
        W, H = 300, 260
        g = _render_ass_gray(font_path, probe, 100, W, H, ffmpeg)
        rows = [y for y in range(H) if any(g[y * W + x] > 128 for x in range(W))] if g else []
        if rows and pil_h > 0:
            scale = pil_h / (rows[-1] - rows[0] + 1)
    except Exception:
        pass
    _ASS_SCALE[key] = scale
    return scale

def ffmpeg_text_shaping(font_path: str = "", ffmpeg: str = "ffmpeg") -> bool:
    """
    True if this FFmpeg draws Tamil correctly the way overlay_engine draws it (libass).
    Without shaping, pre-base vowel signs (ெ ே ை ொ ோ ௌ) are drawn AFTER the consonant, so
    'நம்பிக்கை' is misspelled on screen even with the right font.
    Functional probe: draw 'க' alone and 'கை' at the same x, then find where the lone
    'க' lines up inside 'கை'. Unshaped it sits at offset ~0 (sign drawn after); shaped
    it is pushed right by the width of the pre-base sign ை.
    """
    font = font_path or find_font("", tamil=True)
    key = (ffmpeg, font)
    if key in _SHAPING:
        return _SHAPING[key]
    W, H = 200, 120
    ok = False
    if font and Path(font).exists():
        try:
            ka, kai = (_render_ass_gray(font, t, 64, W, H, ffmpeg) for t in ("\u0B95", "\u0B95\u0BC8"))
            ink = [(x, y) for y in range(H) for x in range(W) if ka and ka[y * W + x] > 128]
            if ink and kai:
                glyph_w = max(x for x, _ in ink) - min(x for x, _ in ink) + 1
                def overlap(dx: int) -> float:
                    return sum(1 for x, y in ink if x + dx < W and kai[y * W + x + dx] > 128) / len(ink)
                best = max(range(0, W // 2), key=overlap)
                ok = best > glyph_w * 0.3          # 'க' found shifted right, past the pre-base sign ை
        except Exception:
            ok = False
    _SHAPING[key] = ok
    return ok

_LOG_PATH: Path | None = None

def set_log(path: Path | str | None) -> None:
    global _LOG_PATH
    _LOG_PATH = Path(path) if path else None
    if _LOG_PATH:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

def _log(text: str) -> None:
    if _LOG_PATH:
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

def _q(s: str) -> str:
    return f'"{s}"' if (" " in s or ";" in s) and not s.startswith('"') else s

def run_ff(cmd: list[str], label: str = "", quiet: bool = False, timeout: int | None = None) -> bool:
    """Run ffmpeg/ffprobe; on failure print the tail of stderr. Always logs."""
    _log(f"\n[{datetime.now():%H:%M:%S}] {label}\n" + " ".join(_q(c) for c in cmd))
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"    ❌  FFmpeg timed out ({label})")
        _log("TIMEOUT")
        return False
    err = r.stderr.decode("utf-8", errors="replace")
    _log(err[-4000:])
    if r.returncode != 0:
        if not quiet:
            print(f"    ❌  FFmpeg error ({label}):\n{err[-1500:]}")
        return False
    return True

def run_ff_capture(cmd: list[str], label: str = "") -> tuple[int, str]:
    """Run and return (returncode, stderr text) — for filters that report via stderr."""
    _log(f"\n[{datetime.now():%H:%M:%S}] {label}\n" + " ".join(_q(c) for c in cmd))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err = r.stderr.decode("utf-8", errors="replace")
    _log(err[-6000:])
    return r.returncode, err

def probe_json(path: Path | str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}

def probe_duration(path: Path | str) -> float:
    d = probe_json(path)
    try:
        return float(d.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        return 0.0

def probe_video_info(path: Path | str) -> dict:
    """{width, height, fps, duration, has_audio, pix_fmt, codec, sample_rate, audio_codec, profile}"""
    d = probe_json(path)
    info = {"width": 0, "height": 0, "fps": 0.0, "duration": 0.0, "has_audio": False,
            "pix_fmt": "", "codec": "", "sample_rate": 0, "audio_codec": "", "profile": "",
            "channels": 0}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not info["width"]:
            info["width"]   = int(s.get("width", 0) or 0)
            info["height"]  = int(s.get("height", 0) or 0)
            info["pix_fmt"] = s.get("pix_fmt", "")
            info["codec"]   = s.get("codec_name", "")
            info["profile"] = s.get("profile", "")
            fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
            try:
                n, dn = fr.split("/")
                info["fps"] = float(n) / float(dn) if float(dn) else 0.0
            except Exception:
                pass
        elif s.get("codec_type") == "audio":
            info["has_audio"]   = True
            info["sample_rate"] = int(s.get("sample_rate", 0) or 0)
            info["audio_codec"] = s.get("codec_name", "")
            info["channels"]    = int(s.get("channels", 0) or 0)
    try:
        info["duration"] = float(d.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        pass
    return info

# ── Colours ───────────────────────────────────────────────────────────────────
def hex_clean(hex_color: str, default: str = "#FFFFFF") -> str:
    h = (str(hex_color) if hex_color else default).strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = default.lstrip("#")
    return "#" + h.upper()

def hex_to_ff(hex_color: str, alpha: float = 1.0, default: str = "#FFFFFF") -> str:
    """'#RRGGBB' → '0xRRGGBB@a' for drawtext / color sources."""
    return f"0x{hex_clean(hex_color, default).lstrip('#')}@{alpha:.2f}"

# ── Logo visibility (pick the variant that reads on this background) ────────
# A brand logo often mixes colours (purple 'U', white 'ni', orange 'Brio'), so one file
# cannot read on every background: on a purple card the purple 'U' vanishes. Each
# visible logo pixel is measured with the WCAG contrast ratio against the background;
# when part of the logo disappears, the best variant from the same folder is used.
LOGO_MIN_CONTRAST = 3.0      # WCAG 1.4.11 non-text contrast (graphics, logos): below 3:1 a logo pixel does not read
LOGO_MAX_HIDDEN = 0.02       # more than 2% of the logo invisible → look for a better variant
LOGO_MIN_TRANSPARENT = 0.30  # variants with an opaque box behind them (< 30% transparent) are not candidates
_LOGO_CACHE: dict = {}

def _rel_lum(rgb) -> float:
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

def hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_clean(hex_color).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def _logo_pixels(path: str):
    """(ink RGB array, transparent share) of a logo, downscaled for speed. Ink = clearly opaque pixels."""
    key = ("px", path, Path(path).stat().st_mtime)
    if key not in _LOGO_CACHE:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGBA")
            im.thumbnail((480, 480))
            a = np.asarray(im, np.float32)
        alpha = a[..., 3]
        _LOGO_CACHE[key] = (a[alpha >= 160][:, :3], float((alpha < 16).mean()))
    return _LOGO_CACHE[key]

def logo_visibility(path: str, backgrounds: list) -> dict:
    """Share of the logo that is invisible, averaged over the backgrounds (one flat card colour, or a grid of
    footage samples behind a corner bug — one bright patch must not condemn a logo), and the worst
    10th-percentile contrast."""
    import numpy as np
    ink, transparent = _logo_pixels(path)
    if ink.size == 0:
        return {"hidden": 1.0, "p10": 1.0, "transparent": transparent}
    c = ink / 255.0
    lin = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    lum = lin @ np.array([0.2126, 0.7152, 0.0722])
    hidden, p10 = 0.0, 99.0
    for bg in backgrounds:
        lb = _rel_lum(hex_rgb(bg) if isinstance(bg, str) else bg)
        ratio = (np.maximum(lum, lb) + 0.05) / (np.minimum(lum, lb) + 0.05)
        hidden += float((ratio < LOGO_MIN_CONTRAST).mean()) / len(backgrounds)
        p10 = min(p10, float(np.percentile(ratio, 10)))
    return {"hidden": hidden, "p10": p10, "transparent": transparent}

def contrast_ratio(a, b) -> float:
    """WCAG contrast ratio between two colours ('#RRGGBB' or (r, g, b))."""
    la = _rel_lum(hex_rgb(a) if isinstance(a, str) else a)
    lb = _rel_lum(hex_rgb(b) if isinstance(b, str) else b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

TEXT_MIN_CONTRAST = 3.0      # WCAG large text (overlays are ≥ 46 px bold)
CTA_BUTTON = "#B85F00"       # deeper Brio orange: white text on it passes (4.5:1); brand orange would not (3.0:1)

def readable_color(bg: str, preferred: str, candidates=None, min_ratio: float = TEXT_MIN_CONTRAST) -> str:
    """The preferred text colour if it reads on bg, else the brand colour that reads best."""
    if contrast_ratio(preferred, bg) >= min_ratio:
        return hex_clean(preferred)
    pool = candidates or [BRAND["white"], BRAND["near_black"], BRAND["orange"], BRAND["purple"]]
    return hex_clean(max(pool, key=lambda c: contrast_ratio(c, bg)))

CTA_ACTIONS = re.compile(r"\b(dm|message|comment|link|click|tap|follow|visit|call|whatsapp|bio|download|"
                         r"sign\s*up|book|demo|join|register|apply|subscribe)\b", re.I)
CTA_BENEFIT = re.compile(r"\b(free|demo|see|live|get|trial|guide|offer|discount|save|price|pricing|learn|start|"
                         r"try|tour|walkthrough|access|template|checklist|consult|call)\b", re.I)
_CTA_STOP = {"a", "an", "the", "to", "for", "and", "or", "me", "us", "now", "it", "your", "our", "on", "in", "at"}

def cta_parts(text: str) -> dict:
    """Split a call to action: {'action': 'DM', 'keyword': 'BRIO', 'rest': ['see', 'live'], 'benefit': True}."""
    toks = (text or "").split()
    action, keyword, ai = "", "", -1
    for i, t in enumerate(toks):
        if CTA_ACTIONS.fullmatch(re.sub(r"[^\w ]", "", t)):
            action, ai = re.sub(r"[^\w]", "", t), i
            break
    for t in toks[ai + 1:] if ai >= 0 else toks:
        core = re.sub(r"[^\w]", "", t)
        if core and (re.match(r"^['\"‘’“”]", t) or (core.isupper() and len(core) >= 2)):
            keyword = core
            break
    rest = [w for w in (re.sub(r"[^\w]", "", t).lower() for t in toks)
            if w and w not in _CTA_STOP and w != action.lower() and w != keyword.lower()]
    benefit = bool(CTA_BENEFIT.search(" ".join(rest))) or len(rest) >= 2
    return {"action": action, "keyword": keyword, "rest": rest, "benefit": benefit}

def variant_suffix(project: dict) -> str:
    """'' for the main render, '_B' for the A/B variant — every file the variant writes carries it."""
    v = str(project.get("variant") or "").strip()
    return f"_{v}" if v else ""

def early_cta(project: dict) -> dict | None:
    """{'shot_id', 'text'} for the small CTA chip on the shot right before the end card, or None.
    Viewers who leave before the end card still see the ask. Setting cta_early (default on)."""
    if not project.get("text", {}).get("cta_early", True):
        return None
    shots = project.get("shots", [])
    for k in range(len(shots) - 1, 0, -1):
        card = shots[k]
        if shot_kind(card) == "logo_card" and (card.get("text_overlay") or "").strip():
            prev = shots[k - 1]
            parts = cta_parts(card["text_overlay"])
            if shot_kind(prev) != "stock" or not parts["action"]:
                return None
            if parts["keyword"]:
                text = f"{parts['action']} '{parts['keyword']}' →"
            else:
                text = " ".join(card["text_overlay"].split()[:3]).rstrip(".,!") + " →"
            return {"shot_id": prev["shot_id"], "text": text}
    return None

def logo_variants(path: str) -> list[str]:
    """The given logo plus every transparent PNG/WebP variant in the same folder."""
    p = Path(path)
    out = [str(p)]
    for f in sorted(p.parent.glob("*")):
        if f.suffix.lower() in (".png", ".webp") and f.resolve() != p.resolve():
            try:
                if _logo_pixels(str(f))[1] >= LOGO_MIN_TRANSPARENT:
                    out.append(str(f))
            except Exception:
                pass
    return out

def best_logo(path: str, backgrounds: list) -> tuple[str, str]:
    """(logo file to use, note). Keeps the given logo while it is fully visible on every background;
    otherwise picks the variant from its folder with the least invisible area, then the highest contrast."""
    if not path or not Path(path).exists() or not backgrounds:
        return path, ""
    try:
        own = logo_visibility(path, backgrounds)
        if own["transparent"] < LOGO_MIN_TRANSPARENT:
            return path, ""          # carries its own background (a badge or photo): always readable, keep the choice
        if own["hidden"] <= LOGO_MAX_HIDDEN:
            return path, ""
        scored = []
        for f in logo_variants(path):
            v = logo_visibility(f, backgrounds)
            scored.append((v["hidden"] > LOGO_MAX_HIDDEN, round(v["hidden"], 3), -v["p10"], f, v))
        scored.sort(key=lambda t: t[:3])
        _, _, _, pick, v = scored[0]
        if pick == path:
            return path, f"{Path(path).name}: {own['hidden']:.0%} of the logo is hard to see and no variant is better"
        return pick, (f"{Path(path).name}: {own['hidden']:.0%} of the logo blends into the background → "
                      f"using {Path(pick).name} ({v['hidden']:.0%} hidden)")
    except Exception as e:
        return path, f"logo visibility check failed ({e.__class__.__name__}) — using {Path(path).name}"

def is_light(hex_color: str) -> bool:
    h = hex_clean(hex_color).lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 140

# ── Text / fonts ──────────────────────────────────────────────────────────────
_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")

def is_tamil(text: str) -> bool:
    return bool(_TAMIL_RE.search(text or ""))

_WIN_FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"

# family → candidate files, best first (bold display weights for overlays)
_FONT_FILES = {
    "inter":           ["Inter-Bold.ttf", "Inter-SemiBold.ttf", "Inter_28pt-Bold.ttf", "Inter-Regular.ttf"],
    "manrope":         ["Manrope-Bold.ttf", "Manrope-ExtraBold.ttf", "Manrope-Regular.ttf"],
    "poppins":         ["Poppins-Bold.ttf", "Poppins-SemiBold.ttf", "Poppins-Regular.ttf"],
    "montserrat":      ["Montserrat-Bold.ttf", "Montserrat-SemiBold.ttf"],
    "arial":           ["arialbd.ttf", "arial.ttf"],
    "segoe ui":        ["segoeuib.ttf", "segoeui.ttf"],
    "calibri":         ["calibrib.ttf", "calibri.ttf"],
    "tahoma":          ["tahomabd.ttf", "tahoma.ttf"],
    "verdana":         ["verdanab.ttf", "verdana.ttf"],
    "noto sans tamil": ["NotoSansTamil-Bold.ttf", "NotoSansTamil-SemiBold.ttf", "NotoSansTamil-Regular.ttf",
                        "NotoSansTamil-VariableFont_wdth,wght.ttf"],
    "catamaran":       ["Catamaran-Bold.ttf", "Catamaran-Regular.ttf"],
    "mukta malar":     ["MuktaMalar-Bold.ttf", "MuktaMalar-Regular.ttf"],
    "nirmala ui":      ["NirmalaB.ttc", "Nirmala.ttc"],
    "latha":           ["lathab.ttf", "latha.ttf"],
    "dejavu sans":     ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"],          # Linux safety net
}
_LATIN_FALLBACK = ["inter", "manrope", "poppins", "montserrat", "segoe ui", "arial", "calibri", "tahoma", "verdana",
                   "dejavu sans"]
_TAMIL_FALLBACK = ["noto sans tamil", "catamaran", "mukta malar", "nirmala ui", "latha"]

def _font_dirs(fonts_folder: str | Path | None) -> list[Path]:
    dirs: list[Path] = []
    if fonts_folder:
        dirs.append(Path(fonts_folder))
    dirs.append(Path(__file__).parent / "fonts")
    dirs.append(_WIN_FONTS)
    dirs += [Path("/usr/share/fonts/truetype/noto"), Path("/usr/share/fonts/truetype/dejavu"),
             Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path(os.path.expanduser("~/.fonts"))]
    return [d for d in dirs if d.exists()]

def _find_in_dirs(files: list[str], dirs: list[Path]) -> str:
    for d in dirs:
        for f in files:
            p = d / f
            if p.exists():
                return str(p)
        try:
            lower = {x.name.lower(): x for x in d.iterdir()}
            for f in files:
                if f.lower() in lower:
                    return str(lower[f.lower()])
        except Exception:
            pass
    return ""

def find_font(preferred: str = "Inter", tamil: bool = False,
              fonts_folder: str | Path | None = None, tamil_preferred: str = "") -> str:
    """
    Resolve a font FILE path (drawtext needs a file, not a family name).
    Latin: project fonts/ → Windows fonts, preferred first, then the brand fallback stack.
    Tamil: Noto Sans Tamil → Catamaran → Mukta Malar → Nirmala UI (ships with Windows) → Latha.
    Returns '' if nothing found (FFmpeg then uses its built-in default — Latin only).
    """
    dirs = _font_dirs(fonts_folder)
    if tamil:
        order = []
        if tamil_preferred:
            if Path(tamil_preferred).exists():
                return str(Path(tamil_preferred))
            order.append(tamil_preferred.lower())
        order += _TAMIL_FALLBACK
        for name in order:
            hit = _find_in_dirs(_FONT_FILES.get(name, [name]), dirs)
            if hit:
                return hit
        return ""
    order = []
    if preferred:
        if Path(preferred).exists():
            return str(Path(preferred))
        order.append(preferred.lower().strip())
    order += [n for n in _LATIN_FALLBACK if n not in order]
    for name in order:
        hit = _find_in_dirs(_FONT_FILES.get(name, [name, name + ".ttf"]), dirs)
        if hit:
            return hit
    return ""

def font_is_family(font_path: str, family: str) -> bool:
    """True if a resolved font file belongs to the named family (e.g. 'Inter-Bold.ttf' → 'Inter')."""
    if not font_path or not family:
        return False
    if Path(family).exists():                         # an explicit font file was configured
        return Path(font_path).name.lower() == Path(family).name.lower()
    name = Path(font_path).name.lower()
    fam = family.lower().strip()
    return name in {f.lower() for f in _FONT_FILES.get(fam, [])} or name.startswith(fam.replace(" ", ""))

def is_tamil_font(font_file: str, tamil_preferred: str = "") -> bool:
    """True if a font file (path or bare name) is one of the known Tamil-capable families."""
    name = Path(font_file or "").name.lower()
    if not name:
        return False
    if tamil_preferred and name == Path(tamil_preferred).name.lower():
        return True
    return any(name in {f.lower() for f in _FONT_FILES.get(fam, [])} for fam in _TAMIL_FALLBACK)

def ff_font_arg(font_path: str) -> str:
    """Escape a font path for use inside a drawtext option: C\\:/Windows/Fonts/x.ttf"""
    return font_path.replace("\\", "/").replace(":", r"\:") if font_path else ""

def ff_escape_text(text: str) -> str:
    """
    Escape text for drawtext inside single quotes in a filtergraph.
    Backslash first. Apostrophes become typographic ’ (a literal ' cannot be
    expressed cleanly inside the filtergraph quoting).
    """
    return (text.replace("\\", "\\\\")
                .replace("'", "\u2019")
                .replace(":", "\\:")
                .replace("%", "\\%")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("[", "\\[")
                .replace("]", "\\]"))

def ff_escape_path(path: str) -> str:
    """Escape a file path used as a filter option value (e.g. movie=, lut3d=)."""
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

def measure_text(font_path: str, size: int, text: str) -> tuple[int, int]:
    """Pixel (width, height) of one line. Uses PIL when available; else estimates."""
    try:
        from PIL import ImageFont
        if font_path and Path(font_path).exists():
            f = ImageFont.truetype(font_path, size)
            l, t, r, b = f.getbbox(text)
            return max(1, r - l), max(1, b - t)
    except Exception:
        pass
    factor = 0.62 if is_tamil(text) else 0.56
    return int(len(text) * size * factor), int(size * 1.15)

def fit_text(text: str, font_path: str, size: int, max_width: int,
             max_lines: int = 2, min_size: int = 40) -> tuple[list[str], int]:
    """
    Word-wrap + shrink so every line fits max_width within max_lines.
    Respects explicit line breaks ('\\n' or the literal two characters \\n from Excel).
    Returns (lines, font_size).
    """
    raw = text.replace("\\n", "\n")
    paragraphs = [p.strip() for p in raw.split("\n") if p.strip()]
    if not paragraphs:
        return [], size

    def wrap(par: str, sz: int) -> list[str]:
        out, cur = [], ""
        for w in par.split():
            trial = (cur + " " + w).strip()
            if not cur or measure_text(font_path, sz, trial)[0] <= max_width:
                cur = trial
            else:
                out.append(cur)
                cur = w
        if cur:
            out.append(cur)
        return out

    def balance(lines: list[str], sz: int) -> list[str]:
        """No one-word orphan on the last line: pull a word down from the line above."""
        if len(lines) >= 2 and len(lines[-1].split()) == 1 and len(lines[-2].split()) >= 3:
            prev = lines[-2].split()
            moved = prev.pop()
            cand_prev, cand_last = " ".join(prev), moved + " " + lines[-1]
            if measure_text(font_path, sz, cand_last)[0] <= max_width:
                lines = lines[:-2] + [cand_prev, cand_last]
        return lines

    sz = size
    while True:
        lines: list[str] = []
        for par in paragraphs:
            lines += wrap(par, sz)
        too_wide = any(measure_text(font_path, sz, ln)[0] > max_width for ln in lines)
        if (len(lines) <= max_lines and not too_wide) or sz <= min_size:
            # If a small shrink (≤ 12%) would save a whole line, take it — fewer lines read faster.
            if len(lines) > 1 and len(paragraphs) == 1:
                for trial in range(sz, int(sz * 0.88) - 1, -2):
                    if trial < min_size:
                        break
                    if measure_text(font_path, trial, paragraphs[0])[0] <= max_width:
                        return [paragraphs[0]], trial
            return balance(lines, sz), sz
        sz -= 4

# ── Shots / treatments ───────────────────────────────────────────────────────
def normalise_shot_id(v) -> str:
    s = str(v).strip() if v not in (None, "") else ""
    if re.fullmatch(r"\d+(\.0)?", s):
        s = str(int(float(s))).zfill(3)
    return s

def infer_asset_type(local_file: str) -> str:
    ext = Path(local_file).suffix.lower() if local_file else ""
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return ""

def shot_kind(shot: dict) -> str:
    """
    card | logo_card | product | stock.
    Explicit 'treatment' (T1–T5) wins; otherwise infer from legacy fields so
    old sheets keep working (a 'logo card' scene with an image_only PNG = T5).
    """
    t = str(shot.get("treatment", "")).strip().upper()
    if t in TREATMENT_KIND:
        return TREATMENT_KIND[t]
    at = str(shot.get("asset_type", "")).lower()
    if at in ("card", "logo_card", "product"):
        return at
    desc = str(shot.get("scene_desc", "")).lower()
    if "logo card" in desc or ("logo" in desc and "cta" in desc):
        return "logo_card"
    if desc.startswith("card:") or desc.startswith("title card") or "blank frame" in desc:
        return "card"
    if "screen recording" in desc or "app screenshot" in desc or "product screen" in desc:
        return "product"
    return "stock"

def shot_treatment(shot: dict) -> str:
    t = str(shot.get("treatment", "")).strip().upper()
    if t in TREATMENT_KIND:
        return t
    k = shot_kind(shot)
    if k == "card":
        return "T1"
    if k == "logo_card":
        return "T5"
    if k == "product":
        return "T4"
    return "T3" if str(shot.get("text_overlay", "")).strip() else "T2"

REAL_TRANSITIONS = ("dissolve", "fade_black", "fade_white")

def transition_overlap(shot: dict) -> float:
    """Seconds this shot's out-transition overlaps the next shot (0 for cut/none)."""
    if shot.get("transition_out", "cut") in REAL_TRANSITIONS:
        try:
            return max(0.0, float(shot.get("trans_dur", 0.0)))
        except (TypeError, ValueError):
            return 0.0
    return 0.0

def compute_timeline(shots: list[dict], durations: list[float] | None = None) -> list[dict]:
    """
    Absolute start/end of each shot inside the ASSEMBLED video, accounting for
    xfade overlaps (a 0.5s dissolve makes the next shot start 0.5s earlier).
    `durations` = actual normalised clip durations (falls back to planned).
    """
    tl = []
    cursor = 0.0
    n = len(shots)
    for i, s in enumerate(shots):
        d = float(durations[i]) if durations and i < len(durations) and durations[i] else float(s.get("duration", 4.0))
        start = cursor
        end = start + d
        ov = transition_overlap(s) if i < n - 1 else 0.0
        tl.append({"shot_id": s["shot_id"], "start": round(start, 3), "end": round(end, 3),
                   "dur": round(d, 3), "transition_out": s.get("transition_out", "cut"),
                   "trans_dur": round(ov, 3), "kind": shot_kind(s), "treatment": shot_treatment(s)})
        cursor = end - ov
    return tl

def timeline_total(tl: list[dict]) -> float:
    return round(tl[-1]["end"], 3) if tl else 0.0

def words(text: str) -> int:
    return len([w for w in re.split(r"\s+", (text or "").replace("\\n", " ").strip()) if w])

def banner(title: str, *lines: str) -> None:
    print(f"\n{'═'*62}\n  {title}")
    for ln in lines:
        print(f"  {ln}")
    print(f"{'═'*62}\n")

def env_key(name: str, fallback: str = "") -> str:
    """API key from the environment first (never commit keys), then the sheet."""
    v = os.environ.get(name, "").strip()
    return v or (fallback or "").strip()

def to_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default

def to_int(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default

def to_bool(v, default: bool = True) -> bool:
    if v in (None, ""):
        return default
    return str(v).strip().lower() not in ("false", "no", "0", "off", "n")
