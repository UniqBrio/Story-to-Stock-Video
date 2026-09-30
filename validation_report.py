#!/usr/bin/env python3
"""
validation_report.py
────────────────────
After Phase 2 (asset_fetcher), reads project.json and generates a single
HTML file showing every downloaded asset side-by-side with its scene
description, keywords, and text overlay.

You review the page in 60–90 seconds: click Keep or Swap on each shot, then
"Save review.json". Then run:
    python run_pipeline.py story_plan.xlsx --apply-review <path to review.json>
Swapped shots are re-fetched and the rejected asset is never offered again.
For anything stock cannot fix: python prompt_generator.py project.json

Output: validation_report.html (in same folder as project.json)

Also does basic sanity checks:
    • File exists and is non-zero
    • Video: probes for duration and resolution (flags if < 1280x720)
    • Image: checks file size (flags if < 50KB — likely a thumbnail)
    • Flags the same asset used on two shots, and the same photographer on two shots

Usage:
    python validation_report.py project.json
    python validation_report.py project.json --out C:/MyProject/review.html
"""

import sys
import json
import html
import base64
import struct
import zlib
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

from svos_common import infer_asset_type

# Box-drawing/emoji output crashes on Windows' default cp1252 console.
# Force UTF-8 so the banners and ✅/❌ markers never abort a run.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── File probers ──────────────────────────────────────────────────────────────

def probe_video(path: Path) -> dict:
    """Return duration, width, height for a video file using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-show_entries", "format=duration,size",
            "-of", "json",
            str(path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        fmt     = data.get("format", {})
        w = h = dur = 0
        for s in streams:
            if s.get("width"):
                w   = int(s.get("width", 0))
                h   = int(s.get("height", 0))
                try:
                    dur = float(s.get("duration", 0))
                except (ValueError, TypeError):
                    pass
        if dur == 0:
            try:
                dur = float(fmt.get("duration", 0))
            except (ValueError, TypeError):
                pass
        size = int(fmt.get("size", path.stat().st_size))
        return {"width": w, "height": h, "duration": round(dur, 2), "size": size, "ok": w > 0}
    except Exception as e:
        return {"width": 0, "height": 0, "duration": 0, "size": 0, "ok": False, "error": str(e)}


def probe_image(path: Path) -> dict:
    """Return approximate width, height, size for an image."""
    size = path.stat().st_size
    w = h = 0
    try:
        # JPEG: scan for SOF marker
        with open(path, "rb") as f:
            data = f.read(min(65536, size))
        if data[:2] == b'\xff\xd8':   # JPEG
            i = 2
            while i < len(data) - 8:
                if data[i] != 0xff:
                    break
                marker = data[i+1]
                length = struct.unpack(">H", data[i+2:i+4])[0]
                if marker in (0xC0, 0xC1, 0xC2):  # SOF0, SOF1, SOF2
                    h = struct.unpack(">H", data[i+5:i+7])[0]
                    w = struct.unpack(">H", data[i+7:i+9])[0]
                    break
                i += 2 + length
        elif data[:8] == b'\x89PNG\r\n\x1a\n':  # PNG
            w = struct.unpack(">I", data[16:20])[0]
            h = struct.unpack(">I", data[20:24])[0]
    except Exception:
        pass
    return {"width": w, "height": h, "size": size, "ok": size > 30000}


def sanity_flags(shot: dict, asset_type: str, probe: dict, target_w: int, target_h: int) -> list:
    """Return list of warning strings for this asset."""
    flags = []
    if not probe.get("ok"):
        flags.append("❌ File unreadable or corrupt")
        return flags

    w = probe.get("width", 0)
    h = probe.get("height", 0)

    if w and h:
        if w < 1280 or h < 720:
            flags.append(f"⚠️ Low resolution: {w}×{h} (min recommended 1280×720)")
        # Aspect ratio check
        target_ratio = target_w / max(target_h, 1)
        asset_ratio  = w / max(h, 1)
        diff = abs(target_ratio - asset_ratio)
        if diff > 0.4:
            flags.append(f"⚠️ Aspect mismatch: asset is {w}×{h} ({'portrait' if h>w else 'landscape'}), video is {'portrait' if target_h>target_w else 'landscape'}")

    if asset_type == "video":
        dur = probe.get("duration", 0)
        shot_dur = float(shot.get("duration", 4.0))
        if dur < shot_dur:
            flags.append(f"⚠️ Video too short: {dur}s (need {shot_dur}s)")

    if asset_type == "image":
        size = probe.get("size", 0)
        if size < 50000:
            flags.append(f"⚠️ Small file size: {size//1024}KB — may be a thumbnail")

    return flags


# ── Image embedding ───────────────────────────────────────────────────────────

def embed_image(path: Path, max_px: int = 540) -> str:
    """
    Base64 data URI of a DOWNSCALED preview. (It used to embed the first 2 MB of
    the file, which cut every large original off half-way — the reviewer judged
    a broken picture.)
    """
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px * 2))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        pass
    try:                                           # no Pillow: ask FFmpeg for a small JPEG instead
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                            "-vf", f"scale='min({max_px},iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-"],
                           capture_output=True, timeout=20)
        if r.returncode == 0 and r.stdout:
            return "data:image/jpeg;base64," + base64.b64encode(r.stdout).decode("ascii")
    except Exception:
        pass
    try:
        if path.stat().st_size <= 2 * 1024 * 1024:  # small enough to embed whole
            mime = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
            return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        pass
    return ""


def video_thumbnail_cmd(video_path: Path, thumb_path: Path) -> bool:
    """Extract first frame of video as JPEG thumbnail using FFmpeg."""
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", "0.5",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "3",
            str(thumb_path),
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        return r.returncode == 0 and thumb_path.exists()
    except Exception:
        return False


# ── Status badge helpers ──────────────────────────────────────────────────────

STATUS_COLOR = {
    "downloaded": "#27ae60",
    "done":       "#2980b9",
    "pending":    "#f39c12",
    "error":      "#c0392b",
    "swap":       "#8e44ad",
    "skipped":    "#666",
}


def grade_pill(grade: str) -> str:
    colors = {
        "chaos": ("#c0392b", "#1a0a0a"),
        "pivot": ("#e67e22", "#1a1200"),
        "cta":   ("#27ae60", "#0a1a0a"),
        "none":  ("#666",    "#111"),
    }
    fg, bg = colors.get(grade, ("#888", "#111"))
    return f'<span style="background:{bg};border:1px solid {fg};color:{fg};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">{grade.upper()}</span>'


# ── Main HTML builder ─────────────────────────────────────────────────────────

def build_report(project: dict, out_path: Path, ffmpeg_available: bool):
    shots        = project.get("shots", [])
    xlsx_name    = Path(project.get("source_xlsx") or "story_plan.xlsx").name
    project_name = project.get("project_name", "My Video")
    target_w     = project.get("width", 1080)
    target_h     = project.get("height", 1920)

    thumb_dir = out_path.parent / "_validation_thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    # Same asset on two shots, or the same photographer on two shots (≈ same shoot)
    by_asset, by_author = {}, {}
    for s_ in shots:
        if s_.get("asset_id"):
            by_asset.setdefault((s_.get("source"), s_.get("asset_id")), []).append(s_.get("shot_id"))
        if s_.get("author") and s_.get("source"):
            by_author.setdefault((s_.get("source"), s_.get("author")), []).append(s_.get("shot_id"))

    total_shots  = len(shots)
    ok_count     = 0
    warn_count   = 0
    error_count  = 0
    missing_count= 0

    cards = ""
    for idx, shot in enumerate(shots, start=1):
        shot_id    = shot.get("shot_id", str(idx))
        scene_desc = shot.get("scene_desc", "")
        keywords   = shot.get("keywords", [])
        local_file = shot.get("local_file", "")
        asset_type = shot.get("asset_type", "")
        inferred   = infer_asset_type(local_file)
        if inferred and asset_type not in ("video", "image"):
            asset_type = inferred
        elif inferred and inferred != asset_type:
            asset_type = inferred                   # trust the file over a mistyped Asset Type column
        source     = shot.get("source", "")
        status     = shot.get("status", "pending")
        grade      = shot.get("color_grade", "none")
        text_over  = shot.get("text_overlay", "")
        trans      = shot.get("transition_out", "cut")
        duration   = shot.get("duration", 4.0)

        file_path = Path(local_file) if local_file else None
        file_exists = file_path and file_path.exists()
        kind = shot.get("kind") or ("logo_card" if "logo card" in scene_desc.lower() else "")
        if kind in ("card", "logo_card") and not file_exists:
            # T1/T5 cards are rendered by clip_normaliser — nothing to download or validate here
            ok_count += 1
            cards += f"""
      <div class="card" style="border-color:#27ae60" id="shot-{html.escape(shot_id)}">
        <div class="meta-row"><span class="shot-num">SHOT {html.escape(shot_id)}</span>
          <span class="status-pill" style="background:#2980b9">{html.escape(kind.upper().replace('_', ' '))}</span>
          <span class="dur-tag">⏱ {duration}s</span></div>
        <div class="scene-text">{html.escape(scene_desc)}</div>
        {"<div class='text-over'>💬 " + html.escape(text_over) + "</div>" if text_over else ""}
        <p class="no-flags">▢ Rendered as a text/logo card by the render layer — no stock asset needed</p>
      </div>"""
            continue

        flags = []
        probe = {}
        img_src = ""
        img_label = ""

        if not file_exists:
            flags.append("❌ File not found — run asset_fetcher.py or generate AI image")
            missing_count += 1
            card_border = "#c0392b"
        else:
            # Probe
            if asset_type == "video":
                probe = probe_video(file_path)
                # Extract thumbnail
                st = file_path.stat()                # keyed on the file, so a swapped asset gets a fresh thumbnail
                thumb_path = thumb_dir / f"thumb_{shot_id}_{int(st.st_mtime)}_{st.st_size}.jpg"
                if not thumb_path.exists() and ffmpeg_available:
                    video_thumbnail_cmd(file_path, thumb_path)
                if thumb_path.exists():
                    img_src = embed_image(thumb_path)
                    img_label = f"Video preview — {probe.get('width',0)}×{probe.get('height',0)} · {probe.get('duration',0)}s"
            else:
                probe = probe_image(file_path)
                img_src = embed_image(file_path)
                img_label = f"Image — {probe.get('width',0)}×{probe.get('height',0)} · {probe.get('size',0)//1024}KB"

            flags = sanity_flags(shot, asset_type, probe, target_w, target_h)
            twins = [x for x in by_asset.get((source, shot.get("asset_id")), []) if x != shot_id]
            if shot.get("asset_id") and twins:
                flags.append(f"❌ Same asset also used on shot {', '.join(twins)}")
            same_author = [x for x in by_author.get((source, shot.get("author")), []) if x != shot_id]
            if shot.get("author") and same_author and not twins:
                flags.append(f"⚠️ Same photographer as shot {', '.join(same_author)} — may look like one shoot")

            if any("❌" in f for f in flags):
                card_border = "#c0392b"
                error_count += 1
            elif flags:
                card_border = "#e67e22"
                warn_count += 1
            else:
                card_border = "#27ae60"
                ok_count += 1

        status_col = STATUS_COLOR.get(status, "#888")

        # Flags HTML
        flags_html = ""
        if flags:
            flag_items = "".join(f"<li>{html.escape(f)}</li>" for f in flags)
            flags_html = f'<ul class="flags">{flag_items}</ul>'
        else:
            flags_html = '<p class="no-flags">✅ No issues detected</p>'

        # Image preview
        img_html = ""
        if img_src:
            img_html = f"""
            <div class="preview-wrap">
              <img src="{img_src}" alt="Shot {html.escape(shot_id)} preview">
              <div class="img-label">{html.escape(img_label)}</div>
            </div>"""
        elif file_exists:
            img_html = '<div class="no-preview">Preview unavailable<br>(video — FFmpeg needed for thumbnail)</div>'
        else:
            img_html = '<div class="no-preview no-file">No file downloaded</div>'

        # Keywords
        kw_pills = " ".join(
            f'<span class="kw-pill">{html.escape(k)}</span>'
            for k in (keywords or [])[:6]
        )

        cards += f"""
      <div class="card" style="border-color:{card_border}" id="shot-{html.escape(shot_id)}">
        <div class="card-top">
          <div class="card-meta">
            <div class="meta-row">
              <span class="shot-num">SHOT {html.escape(shot_id)}</span>
              <span class="status-pill" style="background:{status_col}">{html.escape(status.upper())}</span>
              {grade_pill(grade)}
              <span class="dur-tag">⏱ {duration}s</span>
              <span class="trans-tag">→ {html.escape(trans)}</span>
            </div>

            <div class="scene-text">{html.escape(scene_desc)}</div>

            {"<div class='text-over'>💬 " + html.escape(text_over) + "</div>" if text_over else ""}

            <div class="kw-row">{kw_pills}</div>

            {"<div class='source-tag'>Source: " + html.escape(source) + " · " + html.escape(file_path.name if file_path else "") + "</div>" if file_exists else ""}

            {flags_html}

            <div class="action-row">
              <span class="action-label">Mark as:</span>
              <button class="btn-ok"    onclick="markShot('{html.escape(shot_id)}','ok')">✅ Keep</button>
              <button class="btn-swap"  onclick="markShot('{html.escape(shot_id)}','swap')">🔄 Swap</button>
              <button class="btn-error" onclick="markShot('{html.escape(shot_id)}','error')">❌ No file</button>
            </div>
          </div>

          <div class="preview-col">
            {img_html}
          </div>
        </div>
      </div>"""

    # Summary bar
    total_issues = warn_count + error_count + missing_count
    summary_color = "#27ae60" if total_issues == 0 else "#e67e22" if warn_count > 0 else "#c0392b"

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Asset Validation Report — {html.escape(project_name)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f0f17; color: #e8e4dc;
    padding: 20px; line-height: 1.55;
  }}
  .header {{
    background: linear-gradient(135deg,#1a1a2e,#16213e);
    border:1px solid #DE7D14; border-radius:12px;
    padding:22px 28px; margin-bottom:20px;
  }}
  .header h1 {{ color:#DE7D14; font-size:21px; margin-bottom:4px; }}
  .header p  {{ color:#888; font-size:13px; }}
  .summary {{
    background:#1a1a2e; border:1px solid {summary_color};
    border-radius:10px; padding:16px 22px; margin-bottom:22px;
    display:flex; gap:24px; flex-wrap:wrap; align-items:center;
  }}
  .sum-item {{ font-size:14px; }}
  .sum-ok    {{ color:#27ae60; font-weight:700; }}
  .sum-warn  {{ color:#e67e22; font-weight:700; }}
  .sum-err   {{ color:#c0392b; font-weight:700; }}
  .sum-miss  {{ color:#888;    font-weight:700; }}
  .instructions {{
    background:#111120; border:1px solid #333; border-radius:8px;
    padding:14px 18px; margin-bottom:20px; font-size:13px; color:#aaa;
    line-height:1.7;
  }}
  .instructions strong {{ color:#DE7D14; }}
  .instructions code {{
    background:#1e1e30; color:#DE7D14;
    padding:2px 6px; border-radius:3px; font-size:12px;
  }}
  .card {{
    background:#1a1a2e; border:2px solid #333;
    border-radius:12px; padding:18px 20px;
    margin-bottom:16px; transition:border-color 0.2s;
  }}
  .card-top {{
    display:flex; gap:20px; align-items:flex-start;
  }}
  .card-meta {{ flex:1; min-width:0; }}
  .preview-col {{ flex-shrink:0; width:140px; }}
  .meta-row {{
    display:flex; align-items:center; gap:8px;
    flex-wrap:wrap; margin-bottom:10px;
  }}
  .shot-num {{
    background:#DE7D14; color:#fff; font-weight:700;
    padding:3px 10px; border-radius:5px; font-size:12px;
  }}
  .status-pill {{
    color:#fff; font-weight:700; padding:3px 8px;
    border-radius:5px; font-size:10px;
  }}
  .dur-tag, .trans-tag {{
    color:#666; font-size:12px;
  }}
  .scene-text {{
    font-size:14px; color:#c5b8f0; margin-bottom:8px; font-weight:500;
  }}
  .text-over {{
    font-size:12px; color:#888; margin-bottom:8px;
    border-left:3px solid #DE7D14; padding-left:8px;
  }}
  .kw-row    {{ display:flex; flex-wrap:wrap; gap:4px; margin-bottom:10px; }}
  .kw-pill   {{
    background:#111; border:1px solid #333; color:#888;
    padding:2px 8px; border-radius:4px; font-size:11px;
  }}
  .source-tag {{ font-size:11px; color:#555; margin-bottom:8px; font-family:monospace; }}
  .flags {{ list-style:none; margin-bottom:10px; }}
  .flags li {{ font-size:13px; padding:3px 0; }}
  .no-flags  {{ font-size:13px; color:#27ae60; margin-bottom:10px; }}
  .action-row {{
    display:flex; align-items:center; gap:8px; margin-top:10px;
  }}
  .action-label {{ font-size:12px; color:#666; }}
  button {{ cursor:pointer; border:none; padding:5px 12px; border-radius:5px; font-size:12px; font-weight:600; }}
  .btn-ok    {{ background:#27ae60; color:#fff; }}
  .btn-swap  {{ background:#8e44ad; color:#fff; }}
  .btn-error {{ background:#c0392b; color:#fff; }}
  button:hover {{ opacity:0.85; }}
  .preview-wrap {{ position:relative; }}
  .preview-wrap img {{
    width:100%; height:200px; object-fit:cover;
    border-radius:8px; display:block; border:1px solid #333;
  }}
  .img-label {{
    font-size:10px; color:#555; text-align:center;
    margin-top:4px; font-family:monospace;
  }}
  .no-preview {{
    width:100%; height:140px; background:#111;
    border:1px solid #333; border-radius:8px;
    display:flex; align-items:center; justify-content:center;
    font-size:12px; color:#555; text-align:center; padding:10px;
  }}
  .no-file {{ border-color:#c0392b; color:#c0392b; }}
  .marked-ok   {{ border-color:#27ae60 !important; opacity:0.7; }}
  .marked-swap {{ border-color:#8e44ad !important; }}
  #swap-list {{
    background:#1a1a2e; border:1px solid #8e44ad;
    border-radius:10px; padding:16px 20px; margin-top:24px;
    display:none;
  }}
  #swap-list h3 {{ color:#8e44ad; margin-bottom:10px; }}
  #swap-list li {{ font-size:13px; color:#c5b8f0; margin:4px 0; }}
  .footer {{ text-align:center; color:#333; font-size:11px; margin-top:28px; }}
  @media (max-width:600px) {{
    .card-top {{ flex-direction:column-reverse; }}
    .preview-col {{ width:100%; }}
    .preview-wrap img {{ height:180px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>🔍 Asset Validation Report</h1>
  <p>Project: <strong>{html.escape(project_name)}</strong> &nbsp;|&nbsp;
     {html.escape(str(target_w))}×{html.escape(str(target_h))} &nbsp;|&nbsp;
     Generated: {datetime.now().strftime("%d %b %Y %H:%M")}</p>
</div>

<div class="summary">
  <div class="sum-item">Total shots: <strong>{total_shots}</strong></div>
  <div class="sum-item sum-ok">✅ Clean: {ok_count}</div>
  <div class="sum-item sum-warn">⚠️ Warnings: {warn_count}</div>
  <div class="sum-item sum-err">❌ Errors: {error_count}</div>
  <div class="sum-item sum-miss">📭 Missing: {missing_count}</div>
</div>

<div class="instructions">
  <strong>How to review:</strong><br>
  1. Look at each asset against its scene description. Ask: <em>"Does this image feel like that scene?"</em><br>
  2. Click <strong>✅ Keep</strong> if it works, <strong>🔄 Swap</strong> if it needs replacing, <strong>❌ No file</strong> if nothing was downloaded.<br>
  3. Click <strong>💾 Save review.json</strong> at the bottom (it lands in your Downloads folder).<br>
  4. Run: <code>python run_pipeline.py {html.escape(xlsx_name)} --apply-review &lt;path to review.json&gt;</code><br>
     Swapped shots are re-fetched, the rejected asset is never offered again, and the render continues.<br>
  5. For anything stock cannot fix: <code>python prompt_generator.py project.json</code> → generate AI images.
</div>

{cards}

<div id="swap-list">
  <h3>🔄 Your review — shots to swap / fix</h3>
  <ul id="swap-items"></ul>
  <button onclick="saveReview()" style="margin-top:10px;background:#DE7D14;color:#fff;padding:8px 16px">
    💾 Save review.json
  </button>
  <button onclick="copySwapList()" style="margin-top:10px;background:#444;color:#fff;padding:8px 16px">
    📋 Copy list
  </button>
</div>

<div class="footer">
  UniqBrio Story-to-Video Pipeline · validation_report.py ·
  {total_shots} shots reviewed
</div>

<script>
const swapShots = {{}};
const verdicts = {{}};

function markShot(id, action) {{
  const card = document.getElementById('shot-' + id);
  card.classList.remove('marked-ok','marked-swap');
  verdicts[id] = action === 'ok' ? 'keep' : action;
  if (action === 'ok') {{
    card.style.borderColor = '#27ae60';
    card.style.opacity = '0.65';
    delete swapShots[id];
  }} else if (action === 'swap') {{
    card.style.borderColor = '#8e44ad';
    card.style.opacity = '1';
    swapShots[id] = 'swap';
  }} else {{
    card.style.borderColor = '#c0392b';
    card.style.opacity = '1';
    swapShots[id] = 'error';
  }}
  updateSwapList();
}}

function updateSwapList() {{
  const list = document.getElementById('swap-list');
  const items = document.getElementById('swap-items');
  const keys = Object.keys(swapShots);
  if (Object.keys(verdicts).length === 0) {{
    list.style.display = 'none';
    return;
  }}
  list.style.display = 'block';
  items.innerHTML = keys.map(k =>
    `<li>Shot ${{k}} → set Status = <strong>${{swapShots[k]}}</strong></li>`
  ).join('');
}}

function saveReview() {{
  if (!Object.keys(verdicts).length) {{ alert('Mark at least one shot first.'); return; }}
  const blob = new Blob([JSON.stringify(verdicts, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'review.json';
  document.body.appendChild(a); a.click(); a.remove();
}}

function copySwapList() {{
  const keys = Object.keys(swapShots);
  if (!keys.length) {{ alert('No shots marked for swap yet.'); return; }}
  const text = keys.map(k => `Shot ${{k}} → Status = ${{swapShots[k]}}`).join('\\n');
  navigator.clipboard.writeText(JSON.stringify(verdicts, null, 2)).then(() => {{
    alert('Review copied (paste into review.json):\\n\\n' + text);
  }});
}}
</script>
</body>
</html>"""

    out_path.write_text(html_out, encoding="utf-8")
    return ok_count, warn_count, error_count, missing_count


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML visual validation report for downloaded assets"
    )
    parser.add_argument("project", help="Path to project.json")
    parser.add_argument("--out",   default=None, help="Output HTML path (default: next to project.json)")
    args = parser.parse_args()

    proj_path = Path(args.project)
    if not proj_path.exists():
        sys.exit(f"❌  Not found: {proj_path}")

    project  = json.loads(proj_path.read_text(encoding="utf-8-sig"))
    out_path = Path(args.out) if args.out else proj_path.parent / "validation_report.html"

    # Check if FFmpeg is available for thumbnail extraction
    import shutil
    ffmpeg_ok = bool(shutil.which("ffmpeg"))
    if not ffmpeg_ok:
        print("  ℹ️  FFmpeg not found — video thumbnails will be skipped (images will still preview)")

    shots = project.get("shots", [])
    print(f"\n{'═'*62}")
    print(f"  Validation Report  |  {len(shots)} shots")
    print(f"  Output: {out_path}")
    print(f"{'═'*62}\n")
    print("  Probing assets and generating report...")

    ok, warn, err, miss = build_report(project, out_path, ffmpeg_ok)

    print(f"\n  ✅  Clean   : {ok}")
    print(f"  ⚠️  Warnings: {warn}")
    print(f"  ❌  Errors  : {err}")
    print(f"  📭  Missing : {miss}")
    print(f"\n  📄  Report → {out_path}")
    print(f"\n  Open the HTML file in your browser to review.")
    print(f"\n  AFTER REVIEW:")
    print(f"  • Click Keep / Swap per shot, then 'Save review.json'")
    print(f"  • python run_pipeline.py {Path(project.get('source_xlsx') or 'story_plan.xlsx').name} --apply-review <review.json>")
    print(f"  • python prompt_generator.py project.json        (generate AI prompts)")
    print(f"{'═'*62}\n")


if __name__ == "__main__":
    main()
