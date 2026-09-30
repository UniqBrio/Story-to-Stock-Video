#!/usr/bin/env python3
"""
prompt_generator.py
───────────────────
For every shot in project.json where:
    • status == "error"      (asset_fetcher found nothing)
    • status == "swap"       (you marked it for replacement in Excel)
    • status == "pending"    (not yet fetched — optional, use --all)

Generates a production-ready AI image prompt for each shot based on:
    • Scene description
    • Colour grade (chaos / pivot / cta → maps to mood + lighting)
    • Shot position in story arc (determines emotion level)
    • Keywords already in the shot plan

Outputs:
    1. prompts_report.html   — visual report, copy-paste ready
    2. prompts.json          — machine-readable, one entry per shot
    3. Updates project.json  — adds "ai_prompt" field to each affected shot

Usage:
    python prompt_generator.py project.json
    python prompt_generator.py project.json --all        # include pending shots too
    python prompt_generator.py project.json --shot 003   # single shot
    python prompt_generator.py project.json --tool midjourney
"""

import re
import sys
import json
import html
import argparse
from pathlib import Path
from datetime import datetime

from svos_common import shot_kind, assets_dir, IMAGE_EXT, save_project

# Box-drawing/emoji output crashes on Windows' default cp1252 console.
# Force UTF-8 so the banners and ✅/❌ markers never abort a run.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── Story arc position → emotion intensity ────────────────────────────────────
# We divide shots into thirds: setup / rising / resolution
# and map to lighting, mood, and camera choices

ARC_SETUP      = "setup"       # shots 1 – 33%
ARC_RISING     = "rising"      # shots 34 – 66%
ARC_RESOLUTION = "resolution"  # shots 67 – 100%


def get_arc_position(shot_index: int, total_shots: int) -> str:
    pct = shot_index / max(total_shots - 1, 1)
    if pct < 0.34:
        return ARC_SETUP
    if pct < 0.67:
        return ARC_RISING
    return ARC_RESOLUTION


# ── Grade → cinematic look mapping ───────────────────────────────────────────

GRADE_TO_LOOK = {
    "chaos": {
        "lighting":   "low-key Rembrandt lighting, hard directional key light, deep shadow falloff, cool blue-grey ambient",
        "color":      "desaturated cool tones, teal-grey palette, crushed shadows, lifted blacks",
        "mood":       "tense, heavy, exhausted, overwhelmed",
        "time":       "late night or overcast afternoon",
        "atmosphere": "dim, shadow-heavy, fluorescent flicker, cold window light",
    },
    "pivot": {
        "lighting":   "soft three-point lighting, warm key from camera-left, gentle fill, no harsh shadows",
        "color":      "warm golden tones, lifted shadows, amber-to-cream palette",
        "mood":       "hopeful, determined, quietly confident, turning point",
        "time":       "golden hour or warm indoor light",
        "atmosphere": "warm and inviting, morning light streaming in, dust motes in sunbeam",
    },
    "cta": {
        "lighting":   "high-key bright studio lighting, clean white tones, even exposure, commercial-grade",
        "color":      "vibrant saturated palette, brand colours, clean whites",
        "mood":       "energetic, aspirational, confident, action-ready",
        "time":       "bright midday or studio",
        "atmosphere": "clean, professional, forward-looking, high energy",
    },
    "none": {
        "lighting":   "natural diffused daylight, soft fill, gentle shadows",
        "color":      "neutral colour palette, natural skin tones",
        "mood":       "authentic, documentary, natural",
        "time":       "daytime, natural light",
        "atmosphere": "realistic, honest, unfiltered",
    },
}


# ── Arc position → camera and shot type ──────────────────────────────────────

ARC_TO_CAMERA = {
    ARC_SETUP: {
        "shot_type":   "medium close-up or close-up",
        "lens":        "85mm",
        "angle":       "eye-level or slight low-angle",
        "composition": "rule of thirds, subject left or right of frame",
        "dof":         "shallow depth of field, f/1.8, soft background bokeh",
    },
    ARC_RISING: {
        "shot_type":   "medium shot or medium wide",
        "lens":        "50mm",
        "angle":       "eye-level, slight dutch tilt for tension",
        "composition": "centred composition, dynamic leading lines",
        "dof":         "moderate depth of field, f/2.8",
    },
    ARC_RESOLUTION: {
        "shot_type":   "wide shot or full body",
        "lens":        "35mm",
        "angle":       "low-angle looking up, heroic framing",
        "composition": "centred or golden ratio, subject dominant in frame",
        "dof":         "deep focus, f/5.6, environment visible and clear",
    },
}


# ── Tool-specific suffix ──────────────────────────────────────────────────────

TOOL_SUFFIX = {
    "midjourney": "--ar 9:16 --v 6.1 --stylize 100 --q 2",
    "chatgpt":    "Aspect ratio: 9:16 (portrait). Style: photorealistic.",
    "qwen":       "Portrait orientation 9:16. Photorealistic, cinematic quality.",
    "dalle":      "Portrait 9:16, photorealistic, cinematic lighting, high detail.",
    "gemini":     "Portrait 9:16. Photorealistic cinematic photograph.",
    "firefly":    "Portrait 9:16. Photorealistic. High resolution.",
}

TOOL_FORMAT = {
    "midjourney": "Paste into /imagine in Midjourney Discord or midjourney.com",
    "chatgpt":    "Paste into ChatGPT → DALL·E image generation",
    "qwen":       "Paste into Qwen image generation (tongyi.aliyun.com)",
    "dalle":      "Paste into ChatGPT with DALL·E or via OpenAI API",
    "gemini":     "Paste into Google Gemini image generation",
    "firefly":    "Paste into Adobe Firefly (firefly.adobe.com)",
}

NEGATIVE_BASE = (
    "blurry, out of focus, watermark, text overlay, logo, "
    "deformed hands, extra fingers, bad anatomy, distorted face, "
    "cartoon, illustration, anime, painting, over-saturated, "
    "duplicate, multiple people (unless required), low resolution, "
    "stock photo feel, cheesy, generic, corporate clipart"
)


# ── Core prompt builder ───────────────────────────────────────────────────────

def build_prompt(shot: dict, arc_pos: str, tool: str, video_format: str) -> dict:
    """
    Builds a complete AI image generation prompt for one shot.
    Returns dict with prompt text + metadata.
    """
    shot_id     = shot["shot_id"]
    scene_desc  = shot.get("scene_desc", "")
    keywords    = shot.get("keywords", [])
    grade       = shot.get("color_grade", "none")
    text_over   = shot.get("text_overlay", "")
    duration    = shot.get("duration", 4.0)

    look    = GRADE_TO_LOOK.get(grade, GRADE_TO_LOOK["none"])
    camera  = ARC_TO_CAMERA.get(arc_pos, ARC_TO_CAMERA[ARC_SETUP])
    suffix  = TOOL_SUFFIX.get(tool, TOOL_SUFFIX["chatgpt"])

    # Determine aspect ratio from video format
    if "1080x1920" in video_format or "9:16" in video_format or "reels" in video_format.lower():
        ar_note = "portrait 9:16 vertical format"
    elif "1920x1080" in video_format or "16:9" in video_format:
        ar_note = "landscape 16:9 horizontal format"
        suffix  = (suffix.replace("9:16", "16:9").replace("(portrait)", "(landscape)")
                   .replace("Portrait", "Landscape").replace("portrait", "landscape"))
    else:
        ar_note = "portrait 9:16 vertical format"

    # Build subject line from scene description + keywords
    keyword_str = ", ".join(keywords[:4]) if keywords else scene_desc[:60]

    # Infer whether there's a human subject
    human_words = ["person", "student", "teacher", "coach", "owner", "woman", "man",
                   "child", "kid", "athlete", "parent", "family", "people", "crowd",
                   "vijay", "ananya", "founder", "leader"]
    # whole words only — "personal", "management" and "kidney" are not people
    text_l      = f"{scene_desc} {keyword_str}".lower()
    has_human   = any(re.search(rf"\b{w}s?\b", text_l) for w in human_words)

    if has_human:
        subject_block = (
            f"A South Indian person, natural warm medium-brown skin tone, "
            f"authentic expression showing {look['mood'].split(',')[0].strip()}, "
            f"realistic human proportions, natural body language, "
            f"{scene_desc.lower()}"
        )
    else:
        subject_block = scene_desc

    # Build full prompt
    prompt_lines = [
        # 1. Subject + action
        subject_block,

        # 2. Environment
        f"Setting: {look['time']}, {look['atmosphere']}",

        # 3. Lighting
        f"Lighting: {look['lighting']}",

        # 4. Camera
        (f"Camera: {camera['shot_type']}, {camera['lens']} lens, "
         f"{camera['angle']}, {camera['composition']}, {camera['dof']}"),

        # 5. Color + mood
        f"Color grade: {look['color']}. Mood: {look['mood']}",

        # 6. Style
        "Style: photorealistic cinematic photograph, film still quality, "
        "authentic Indian context, no stock photo feel, natural and raw",

        # 7. Quality
        "Quality: 8K ultra-detailed, sharp focus, professional colour grading, "
        "masterpiece, award-winning photography",
    ]

    # Negative prompt
    negative = NEGATIVE_BASE
    if has_human:
        negative += ", ugly, mutated, cloned face, unrealistic eyes, plastic skin"

    prompt_text = ". ".join(prompt_lines)

    # Add tool suffix
    if tool == "midjourney":
        full_prompt = f"{prompt_text} --no {negative} {suffix}"
    else:
        full_prompt = f"{prompt_text}\n\nNegative: {negative}\n{suffix}"

    # Filename suggestion
    kw_slug = "_".join(keywords[0].split()[:3]).lower() if keywords else "scene"
    kw_slug = "".join(c if c.isalnum() or c == "_" else "" for c in kw_slug)
    save_as = f"shot_{shot_id}_{kw_slug}.jpg"

    return {
        "shot_id":      shot_id,
        "scene_desc":   scene_desc,
        "arc_position": arc_pos,
        "grade":        grade,
        "tool":         tool,
        "tool_note":    TOOL_FORMAT.get(tool, ""),
        "prompt":       full_prompt,
        "save_as":      save_as,
        "save_folder":  "Assets/Images/",
        "text_overlay": text_over,
        "duration":     duration,
        "keywords":     keywords,
    }


# ── HTML report builder ───────────────────────────────────────────────────────

def build_html(prompts: list[dict], project_name: str, tool: str) -> str:
    tool_note = TOOL_FORMAT.get(tool, "")
    rows = ""
    for i, p in enumerate(prompts, start=1):
        arc_badge_color = {
            ARC_SETUP:      "#c0392b",
            ARC_RISING:     "#e67e22",
            ARC_RESOLUTION: "#27ae60",
        }.get(p["arc_position"], "#888")

        escaped_prompt = html.escape(p["prompt"])

        rows += f"""
        <div class="card">
          <div class="card-header">
            <span class="shot-badge">SHOT {html.escape(p['shot_id'])}</span>
            <span class="arc-badge" style="background:{arc_badge_color}">
              {p['arc_position'].upper()}
            </span>
            <span class="grade-badge grade-{p['grade']}">{p['grade'].upper()}</span>
            <span class="dur-badge">⏱ {p['duration']}s</span>
          </div>

          <div class="scene-desc">
            <strong>Scene:</strong> {html.escape(p['scene_desc'])}
          </div>

          {"<div class='text-overlay-note'>💬 Text overlay: <em>" + html.escape(p['text_overlay']) + "</em></div>" if p['text_overlay'] else ""}

          <div class="prompt-label">
            📋 AI IMAGE PROMPT
            <span class="tool-label">→ {html.escape(p['tool'].upper())}</span>
          </div>
          <div class="prompt-box">
            <textarea id="prompt_{i}" readonly onclick="this.select()"
                      rows="8">{escaped_prompt}</textarea>
            <button onclick="copyPrompt('prompt_{i}', this)">📋 Copy</button>
          </div>

          <div class="save-instruction">
            <strong>After generating:</strong> Save as
            <code>{html.escape(p['save_as'])}</code> into
            <code>{html.escape(p['save_folder'])}</code>
            then set Status = <strong>downloaded</strong> in Excel
          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Prompt Generator — {html.escape(project_name)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f0f17;
    color: #e8e4dc;
    padding: 24px;
    line-height: 1.6;
  }}
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border: 1px solid #DE7D14;
    border-radius: 12px;
    padding: 24px 32px;
    margin-bottom: 28px;
  }}
  .header h1 {{ color: #DE7D14; font-size: 22px; margin-bottom: 6px; }}
  .header p  {{ color: #888; font-size: 14px; }}
  .meta-row  {{ display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px; }}
  .meta-tag  {{
    background: #1e1e30; border: 1px solid #333;
    border-radius: 6px; padding: 4px 10px; font-size: 12px; color: #aaa;
  }}
  .tool-banner {{
    background: #1a1a2e; border: 1px solid #6708C0;
    border-radius: 8px; padding: 14px 20px; margin-bottom: 24px;
    font-size: 14px; color: #c5b8f0;
  }}
  .tool-banner strong {{ color: #DE7D14; }}
  .card {{
    background: #1a1a2e;
    border: 1px solid #2a2a40;
    border-radius: 12px;
    padding: 22px 24px;
    margin-bottom: 20px;
    transition: border-color 0.2s;
  }}
  .card:hover {{ border-color: #DE7D14; }}
  .card-header {{
    display: flex; align-items: center; gap: 10px;
    flex-wrap: wrap; margin-bottom: 14px;
  }}
  .shot-badge {{
    background: #DE7D14; color: #fff; font-weight: 700;
    padding: 4px 12px; border-radius: 6px; font-size: 13px;
  }}
  .arc-badge {{
    color: #fff; font-weight: 600;
    padding: 4px 10px; border-radius: 6px; font-size: 11px;
  }}
  .grade-badge {{
    font-weight: 700; padding: 4px 10px; border-radius: 6px; font-size: 11px;
  }}
  .grade-chaos  {{ background: #1a1a2e; border: 1px solid #c0392b; color: #c0392b; }}
  .grade-pivot  {{ background: #1a1a2e; border: 1px solid #e67e22; color: #e67e22; }}
  .grade-cta    {{ background: #1a1a2e; border: 1px solid #27ae60; color: #27ae60; }}
  .grade-none   {{ background: #1a1a2e; border: 1px solid #666;    color: #888;    }}
  .dur-badge    {{ color: #888; font-size: 12px; margin-left: auto; }}
  .scene-desc   {{ font-size: 14px; color: #c5b8f0; margin-bottom: 10px; }}
  .text-overlay-note {{
    font-size: 12px; color: #888; margin-bottom: 10px;
    border-left: 3px solid #DE7D14; padding-left: 10px;
  }}
  .prompt-label {{
    font-size: 11px; font-weight: 700; color: #888; letter-spacing: 1px;
    text-transform: uppercase; margin-bottom: 8px;
  }}
  .tool-label {{
    background: #6708C0; color: #fff; padding: 2px 8px;
    border-radius: 4px; font-size: 10px; margin-left: 8px;
  }}
  .prompt-box   {{ position: relative; margin-bottom: 12px; }}
  textarea {{
    width: 100%; background: #0f0f17; border: 1px solid #333;
    border-radius: 8px; color: #e8e4dc; padding: 14px;
    font-size: 13px; line-height: 1.6; resize: vertical;
    font-family: 'Courier New', monospace; cursor: pointer;
  }}
  textarea:focus {{ outline: none; border-color: #DE7D14; }}
  button {{
    position: absolute; top: 10px; right: 10px;
    background: #DE7D14; color: #fff; border: none;
    padding: 6px 14px; border-radius: 6px; cursor: pointer;
    font-size: 12px; font-weight: 600;
  }}
  button:hover {{ background: #c56d10; }}
  button.copied {{ background: #27ae60; }}
  .save-instruction {{
    font-size: 12px; color: #666;
    background: #111120; border-radius: 6px; padding: 10px 14px;
    border-left: 3px solid #27ae60;
  }}
  .save-instruction code {{
    background: #1e1e30; color: #DE7D14;
    padding: 2px 6px; border-radius: 3px; font-size: 11px;
  }}
  .footer {{
    text-align: center; color: #444; font-size: 12px; margin-top: 32px;
  }}
  @media (max-width: 600px) {{
    body {{ padding: 14px; }}
    .dur-badge {{ margin-left: 0; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>🎨 AI Image Prompt Generator</h1>
  <p>Project: <strong>{html.escape(project_name)}</strong> &nbsp;|&nbsp;
     Generated: {datetime.now().strftime("%d %b %Y %H:%M")}</p>
  <div class="meta-row">
    <span class="meta-tag">📸 {len(prompts)} shots need AI images</span>
    <span class="meta-tag">🔧 Tool: {html.escape(tool.upper())}</span>
  </div>
</div>

<div class="tool-banner">
  <strong>How to use:</strong> {html.escape(tool_note)}.
  Click the textarea to select all text, then click <strong>Copy</strong>.
  After generating, save the image with the filename shown below each prompt
  and set Status = <code>downloaded</code> in your Excel sheet.
</div>

{rows}

<div class="footer">
  UniqBrio Story-to-Video Pipeline · prompt_generator.py
</div>

<script>
function copyPrompt(id, btn) {{
  const el = document.getElementById(id);
  el.select();
  document.execCommand('copy');
  btn.textContent = '✅ Copied!';
  btn.classList.add('copied');
  setTimeout(() => {{
    btn.textContent = '📋 Copy';
    btn.classList.remove('copied');
  }}, 2000);
}}
</script>
</body>
</html>"""


# ── Link generated images back into the plan ─────────────────────────────────

def link_images(project: dict, proj_path: Path, images_dir: Path) -> None:
    """
    Wire generated images into project.json so the render picks them up (and phase 1
    carries them forward): local_file, asset_type=image, status=downloaded, and an
    AI-generated provenance note for the licence log (platforms ask for disclosure).
    """
    files = sorted(f for f in images_dir.glob("shot_*") if f.suffix.lower() in IMAGE_EXT) if images_dir.exists() else []
    linked = []
    for shot in project.get("shots", []):
        if shot_kind(shot) != "stock":
            continue
        mine = [f for f in files if re.match(rf"shot_{re.escape(shot['shot_id'])}(_|\.)", f.name)]
        if not mine:
            continue
        newest = max(mine, key=lambda f: f.stat().st_mtime)
        if shot.get("local_file") == str(newest) and shot.get("status") == "downloaded":
            continue
        if shot.get("source") not in ("", None, "ai-generated") and shot.get("status") == "downloaded" \
                and shot.get("local_file") and Path(shot["local_file"]).exists() and shot.get("local_file") != str(newest):
            # a working stock asset is in place; only replace it if the image is newer than the stock file
            if newest.stat().st_mtime <= Path(shot["local_file"]).stat().st_mtime:
                continue
        for f in ("asset_id", "asset_url", "page_url", "author", "asset_score", "query_used", "approved"):
            shot.pop(f, None)
        shot.update({"local_file": str(newest), "asset_type": "image", "status": "downloaded",
                     "source": "ai-generated", "license": "AI-generated image — disclose as AI content where the platform requires",
                     "author": f"{shot.get('prompt_tool', 'AI')} (prompt in ai_prompt)"})
        if shot.get("ken_burns", "none") == "none":
            shot["ken_burns"] = "zoom_in"          # a still without motion freezes the frame (QA flags it)
        linked.append(f"{shot['shot_id']} ← {newest.name}")
    save_project(project, proj_path)
    print(f"\n  Linked {len(linked)} generated image(s) from {images_dir}")
    for ln in linked:
        print(f"    ✅  {ln}")
    if not linked:
        print("    (none found — save files as shot_<ID>_<anything>.jpg, e.g. shot_003_calm_desk.jpg)")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate AI image prompts for shots with no/bad stock assets"
    )
    parser.add_argument("project",  help="Path to project.json")
    parser.add_argument("--all",    action="store_true",
                        help="Include pending shots (not just error/swap)")
    parser.add_argument("--shot",   default=None,
                        help="Process a single shot ID")
    parser.add_argument("--tool",   default="chatgpt",
                        choices=list(TOOL_SUFFIX.keys()),
                        help="Target AI image tool (default: chatgpt)")
    parser.add_argument("--out",    default=None,
                        help="Output folder (default: same folder as project.json)")
    parser.add_argument("--link",   action="store_true",
                        help="Attach generated images saved as shot_XXX_*.jpg/png/webp in Assets/Images to their shots")
    args = parser.parse_args()

    proj_path = Path(args.project)
    if not proj_path.exists():
        sys.exit(f"❌  Not found: {proj_path}")

    project      = json.loads(proj_path.read_text(encoding="utf-8-sig"))
    all_shots    = project.get("shots", [])
    images_dir   = assets_dir(project, proj_path) / "Images"
    if args.link:
        link_images(project, proj_path, images_dir)
        return
    project_name = project.get("project_name", "My Video")
    video_format = f"{project.get('width', 1080)}x{project.get('height', 1920)}"
    out_dir      = Path(args.out) if args.out else proj_path.parent

    # Filter shots that need prompts
    target_statuses = {"error", "swap"}
    if args.all:
        target_statuses.add("pending")

    # Cards (T1/T5) are rendered by the pipeline and product inserts come from screen
    # recordings — neither ever needs a generated photo.
    stock_shots = [s for s in all_shots if shot_kind(s) == "stock"]
    if args.shot:
        shots_to_process = [s for s in stock_shots if s["shot_id"] == args.shot]
    else:
        shots_to_process = [
            s for s in stock_shots
            if s.get("status", "pending") in target_statuses
            or not s.get("local_file")
        ]

    if not shots_to_process:
        print("\n  ✅  No shots need AI image prompts.")
        print("     (All shots have downloaded assets)")
        print("     Use --all to generate prompts for pending shots too.\n")
        return

    total = len(all_shots)
    print(f"\n{'═'*62}")
    print(f"  Prompt Generator  |  {len(shots_to_process)} shots  |  tool: {args.tool.upper()}")
    print(f"{'═'*62}\n")

    prompts = []
    index_of = {s["shot_id"]: i for i, s in enumerate(all_shots)}
    for shot in shots_to_process:
        idx     = index_of[shot["shot_id"]]
        arc_pos = get_arc_position(idx, total)
        p       = build_prompt(shot, arc_pos, args.tool, video_format)
        p["save_folder"] = str(images_dir)
        prompts.append(p)

        # Store back in shot
        shot["ai_prompt"]   = p["prompt"]
        shot["prompt_tool"] = args.tool
        print(f"  ✅  {p['shot_id']}  [{p['arc_position']}]  grade={p['grade']}")

    # Write HTML report
    html_path = out_dir / "prompts_report.html"
    html_path.write_text(build_html(prompts, project_name, args.tool), encoding="utf-8")
    print(f"\n  📄  HTML report → {html_path}")

    # Write JSON
    json_path = out_dir / "prompts.json"
    json_path.write_text(
        json.dumps({"project": project_name, "tool": args.tool, "prompts": prompts},
                   indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"  📄  JSON data   → {json_path}")

    # Update project.json
    proj_path.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  📄  project.json updated with ai_prompt fields")

    print(f"\n{'═'*62}")
    print(f"  NEXT STEPS:")
    print(f"  1. Open prompts_report.html in your browser")
    print(f"  2. Copy each prompt → generate in {args.tool.upper()}")
    print(f"  3. Save each image as shown (shot_XXX_keyword.jpg) into:")
    print(f"     {images_dir}")
    print(f"  4. Link them to their shots:  python prompt_generator.py {proj_path.name} --link")
    print(f"  5. Continue the pipeline from Phase 3:")
    print(f"     python run_pipeline.py {Path(project.get('source_xlsx') or 'story_plan.xlsx').name} --from 3")
    print(f"{'═'*62}\n")


if __name__ == "__main__":
    main()
