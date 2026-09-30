#!/usr/bin/env python3
"""
bgm_prompt_generator.py
───────────────────────
Reads project.json (produced by story_reader.py) and generates a detailed,
production-ready BGM creation prompt for AI music tools such as:
    • Google Gemini (Lyria 3) — gemini.google.com/app  ← single natural-language prompt
    • Suno AI     (suno.com)
    • Udio        (udio.com)
    • Stable Audio (stability.ai)
    • Soundraw    (soundraw.io)

The prompt is built by analysing:
    • Total video duration
    • Shot-by-shot colour grade arc (chaos → pivot → cta = emotional arc)
    • Story arc position of each shot (setup / rising / resolution)
    • Shot durations (determines tempo and cut energy)
    • Text overlays (reveals emotional moments for musical peaks)
    • Target platform (Reels = upbeat but controlled, YouTube = more cinematic)

Outputs:
    1. bgm_prompts.html   — visual report with copy-paste prompts per tool
    2. bgm_brief.json     — structured brief for reference
    3. Updates project.json — adds "bgm_brief" field
    4. Prints BGM file path placeholder to paste into Project Settings

Usage:
    python bgm_prompt_generator.py project.json
    python bgm_prompt_generator.py project.json --tool gemini
    python bgm_prompt_generator.py project.json --tool suno
    python bgm_prompt_generator.py project.json --tool udio
    python bgm_prompt_generator.py project.json --all-tools
"""

import re
import sys
import math
import json
import html
import argparse
from pathlib import Path
from datetime import datetime

# Box-drawing/emoji output crashes on Windows' default cp1252 console.
# Force UTF-8 so the banners and ✅/❌ markers never abort a run.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── Emotional arc analyser ────────────────────────────────────────────────────

ARC_SETUP      = "setup"
ARC_RISING     = "rising"
ARC_RESOLUTION = "resolution"

def get_arc_position(idx: int, total: int) -> str:
    pct = idx / max(total - 1, 1)
    if pct < 0.34:  return ARC_SETUP
    if pct < 0.67:  return ARC_RISING
    return ARC_RESOLUTION

GRADE_EMOTION = {
    "chaos": {
        "emotion":    "tension, exhaustion, overwhelm, struggle",
        "energy":     "low",
        "key":        "minor",
        "brightness": "dark",
        "tempo_adj":  "slow and heavy",
    },
    "pivot": {
        "emotion":    "hope, turning point, quiet confidence, realisation",
        "energy":     "building",
        "key":        "minor resolving to major",
        "brightness": "warming",
        "tempo_adj":  "gradually lifting",
    },
    "cta": {
        "emotion":    "aspiration, energy, confidence, action",
        "energy":     "high",
        "key":        "major",
        "brightness": "bright and open",
        "tempo_adj":  "uplifting and resolved",
    },
    "none": {
        "emotion":    "neutral, documentary, authentic",
        "energy":     "moderate",
        "key":        "major or neutral",
        "brightness": "natural",
        "tempo_adj":  "steady",
    },
}

# Transition cadence mapping
TRANSITION_ENERGY = {
    "cut":        "sharp",
    "dissolve":   "smooth",
    "fade_black": "heavy pause",
    "fade_white": "breath and release",
    "none":       "hold",
}


# ── Story arc analyser ────────────────────────────────────────────────────────

def analyse_arc(shots: list, timeline: list | None = None) -> dict:
    """
    Walk through the shot list and extract the emotional arc,
    dominant grade, tempo cues, and key musical moments.
    `timeline` (from transition_engine) gives real start times after transition overlaps.
    """
    total = len(shots)
    grades   = [s.get("color_grade", "none") for s in shots]
    durations= [float(s.get("duration", 4.0)) for s in shots]
    tl_start = {t["shot_id"]: t for t in (timeline or [])}
    texts    = [s.get("text_overlay", "") for s in shots]
    trans    = [s.get("transition_out", "cut") for s in shots]

    total_dur = sum(durations)

    # Build arc segments
    segments = []
    time_cursor = 0.0
    for i, shot in enumerate(shots):
        arc_pos = get_arc_position(i, total)
        dur     = durations[i]
        grade   = grades[i]
        emotion = GRADE_EMOTION.get(grade, GRADE_EMOTION["none"])
        t = tl_start.get(shot.get("shot_id"))
        if t:
            time_cursor, dur = float(t["start"]), float(t["dur"])
        segments.append({
            "shot_id":      shot.get("shot_id", str(i+1)),
            "start_time":   round(time_cursor, 2),
            "end_time":     round(time_cursor + dur, 2),
            "duration":     dur,
            "arc_position": arc_pos,
            "grade":        grade,
            "emotion":      emotion["emotion"],
            "energy":       emotion["energy"],
            "transition":   trans[i],
            "text":         texts[i],
        })
        time_cursor += dur - (float(t["trans_dur"]) if t else 0.0)
    if timeline:
        total_dur = float(timeline[-1]["end"])

    # Dominant grade per arc zone
    setup_grades    = [s["grade"] for s in segments if s["arc_position"] == ARC_SETUP]
    rising_grades   = [s["grade"] for s in segments if s["arc_position"] == ARC_RISING]
    res_grades      = [s["grade"] for s in segments if s["arc_position"] == ARC_RESOLUTION]

    def dominant(lst):
        if not lst:
            return "none"
        return max(set(lst), key=lst.count)

    dom_setup   = dominant(setup_grades)
    dom_rising  = dominant(rising_grades)
    dom_res     = dominant(res_grades)

    # Detect emotional arc type
    if dom_setup == "chaos" and dom_res in ("cta", "pivot"):
        arc_type = "problem_to_solution"
    elif dom_setup == "pivot" and dom_res == "cta":
        arc_type = "aspiration_build"
    elif dom_setup == "chaos" and dom_rising == "chaos":
        arc_type = "sustained_tension"
    elif dom_res == "cta":
        arc_type = "call_to_action"
    else:
        arc_type = "neutral_journey"

    # Find key musical moments
    key_moments = []
    for s in segments:
        if s["grade"] == "pivot" and s["arc_position"] == ARC_RISING:
            key_moments.append({
                "time": s["start_time"],
                "type": "pivot_moment",
                "note": f"Emotional pivot at {s['start_time']}s — music should brighten here",
            })
        if s["grade"] == "cta" and not any(k["type"] == "cta_moment" for k in key_moments):
            key_moments.append({
                "time": s["start_time"],
                "type": "cta_moment",
                "note": f"CTA begins at {s['start_time']}s — music should be at peak energy",
            })
        if s["transition"] in ("fade_black", "fade_white"):
            key_moments.append({
                "time": s["end_time"],
                "type": "breath_moment",
                "note": f"Fade transition at {s['end_time']}s — consider musical breath or micro-pause",
            })

    # Average shot duration → cut energy → tempo feel
    avg_dur = total_dur / max(total, 1)
    if avg_dur < 2.5:
        tempo_feel = "fast cuts (avg {:.1f}s/shot) → punchy, rhythmic".format(avg_dur)
        bpm_range  = "110–130 BPM"
    elif avg_dur < 4.5:
        tempo_feel = "medium cuts (avg {:.1f}s/shot) → flowing, moderate energy".format(avg_dur)
        bpm_range  = "80–110 BPM"
    else:
        tempo_feel = "slow cuts (avg {:.1f}s/shot) → cinematic, atmospheric".format(avg_dur)
        bpm_range  = "60–85 BPM"

    return {
        "total_duration":   round(total_dur, 2),
        "total_shots":      total,
        "arc_type":         arc_type,
        "dominant_setup":   dom_setup,
        "dominant_rising":  dom_rising,
        "dominant_resolve": dom_res,
        "avg_shot_dur":     round(avg_dur, 2),
        "tempo_feel":       tempo_feel,
        "bpm_range":        bpm_range,
        "key_moments":      key_moments,
        "segments":         segments,
    }


# ── BGM brief builder ─────────────────────────────────────────────────────────

ARC_TYPE_MUSIC = {
    "problem_to_solution": {
        "style":       "cinematic orchestral — begins sparse and melancholic, builds to uplifting resolution",
        "instruments": "sparse piano intro, subtle strings building, light percussion entering at pivot, full arrangement at CTA",
        "mood_arc":    "heavy → hopeful → triumphant",
        "tags":        "emotional, cinematic, inspirational, building, hopeful, academy",
    },
    "aspiration_build": {
        "style":       "uplifting acoustic-cinematic — warm from the start, steadily building to peak energy",
        "instruments": "acoustic guitar or warm piano, strings, light drums building, full arrangement at peak",
        "mood_arc":    "warm → motivated → triumphant",
        "tags":        "uplifting, warm, motivational, building, hopeful, commercial",
    },
    "sustained_tension": {
        "style":       "dark ambient cinematic — sustained low energy, minimal and tense throughout",
        "instruments": "low strings, subtle bass pulse, sparse piano, no drums until resolution",
        "mood_arc":    "tense → tenser → sudden release",
        "tags":        "tense, dark, ambient, cinematic, minimal, dramatic",
    },
    "call_to_action": {
        "style":       "bright commercial cinematic — energetic from start, confident and clean",
        "instruments": "clean piano or synth lead, driving rhythm, bright strings, strong beat",
        "mood_arc":    "energetic → peak → strong close",
        "tags":        "commercial, upbeat, confident, professional, clean, energetic",
    },
    "neutral_journey": {
        "style":       "warm neutral cinematic — steady emotional warmth throughout, no dramatic shifts",
        "instruments": "acoustic piano, light strings, subtle percussion",
        "mood_arc":    "calm → engaged → satisfied",
        "tags":        "warm, neutral, cinematic, authentic, steady, natural",
    },
}


def music_seconds(brief: dict) -> int:
    """Whole seconds of music to ask for — rounded UP, so a 47.8 s video never ends in 0.8 s of silence."""
    return int(math.ceil(round(float(brief["total_duration"]), 3)))


def _cue_time(cue: str) -> float:
    """
    Sort key for a timeline cue string. Extracts the leading timestamp so cues
    order chronologically; key moments (⚑) and unparseable cues sort to the end.
    Handles both "m:ss — ..." and "12.5s — ..." prefixes robustly.
    """
    if cue.startswith("⚑"):                       # legacy form: "⚑  … at 12.5s …"
        m = re.search(r"at\s+([0-9]*\.?[0-9]+)\s*s", cue)
        return float(m.group(1)) if m else 99999.0
    m = re.match(r"\s*(\d+):(\d+)", cue)          # m:ss form, e.g. "0:00 — ..."
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"\s*([0-9]*\.?[0-9]+)\s*s", cue)  # seconds form, e.g. "12.5s — ..."
    if m:
        return float(m.group(1))
    return 0.0


def build_bgm_brief(analysis: dict, project: dict) -> dict:
    arc_type   = analysis["arc_type"]
    music_data = ARC_TYPE_MUSIC.get(arc_type, ARC_TYPE_MUSIC["neutral_journey"])
    total_dur  = analysis["total_duration"]
    bpm_range  = analysis["bpm_range"]

    # Build timeline cue list
    timeline_cues = []
    prev_arc = None
    for seg in analysis["segments"]:
        arc = seg["arc_position"]
        if arc != prev_arc:
            cue_map = {
                ARC_SETUP: {
                    "chaos":  f"0:00 — Begin with {GRADE_EMOTION['chaos']['tempo_adj']} feel. Sparse, minimal, emotional weight.",
                    "pivot":  f"0:00 — Begin warm and optimistic. Soft and inviting.",
                    "cta":    f"0:00 — Begin energetic and bright. Commercial confidence.",
                    "none":   f"0:00 — Begin natural and steady. Documentary warmth.",
                },
                ARC_RISING: {
                    "chaos":  f"{seg['start_time']}s — Sustain tension. Add subtle movement but no resolution yet.",
                    "pivot":  f"{seg['start_time']}s — Begin brightening. Strings or piano lifting. Key change possible.",
                    "cta":    f"{seg['start_time']}s — Energy building. Percussion enters or intensifies.",
                    "none":   f"{seg['start_time']}s — Maintain steady warmth. Slight elevation.",
                },
                ARC_RESOLUTION: {
                    "chaos":  f"{seg['start_time']}s — No resolution — hold tension to end.",
                    "pivot":  f"{seg['start_time']}s — Full warm resolution. Major key arrival.",
                    "cta":    f"{seg['start_time']}s — PEAK ENERGY. Full arrangement, bright, driving. Music at maximum.",
                    "none":   f"{seg['start_time']}s — Satisfying close. Natural ending.",
                },
            }
            grade = seg["grade"]
            cue   = cue_map.get(arc, {}).get(grade, f"{seg['start_time']}s — continue")
            timeline_cues.append(cue)
            prev_arc = arc

    # Fade out
    fade_start = max(0, total_dur - 3.0)
    timeline_cues.append(f"{fade_start:.0f}s — Begin fade out over final 3 seconds")
    timeline_cues.append(f"{total_dur:.0f}s — Music ends (matched to video length)")

    # Add key musical moments
    for km in analysis["key_moments"]:
        timeline_cues.append(f"{km['time']:g}s ⚑ {km['note']}")
    timeline_cues.sort(key=_cue_time)

    return {
        "project_name":    project.get("project_name", "My Video"),
        "total_duration":  total_dur,
        "arc_type":        arc_type,
        "style":           music_data["style"],
        "instruments":     music_data["instruments"],
        "mood_arc":        music_data["mood_arc"],
        "bpm_range":       bpm_range,
        "tempo_feel":      analysis["tempo_feel"],
        "tags":            music_data["tags"],
        "timeline_cues":   timeline_cues,
        "key_moments":     analysis["key_moments"],
        "platform":        {"reels": "Instagram Reels", "instagram": "Instagram Reels", "shorts": "YouTube Shorts",
                            "tiktok": "TikTok", "youtube": "YouTube", "landscape": "YouTube", "16:9": "YouTube"}.get(
                               str(project.get("platform", "")).lower(),
                               "Instagram Reels" if project.get("height", 1920) > project.get("width", 1080) else "YouTube"),
        "audio_mode":      "B (Music + Captions)" if not project.get("audio", {}).get("vo_path") else "B + VO overlay",
    }


# ── Tool-specific prompt builders ─────────────────────────────────────────────

def build_suno_prompt(brief: dict) -> str:
    dur_secs = music_seconds(brief)
    mins, secs = divmod(dur_secs, 60)
    dur_str = f"{mins}:{secs:02d}"
    cues_str = "\n".join(f"  • {c}" for c in brief["timeline_cues"])
    return f"""[STYLE]
{brief['style']}

[MOOD]
{brief['mood_arc']}

[INSTRUMENTS]
{brief['instruments']}

[TEMPO]
{brief['bpm_range']} — {brief['tempo_feel']}

[DURATION]
{dur_str} (exactly {dur_secs} seconds — must match video length)

[STRUCTURE — follow this timeline precisely]
{cues_str}

[TAGS / GENRE KEYWORDS]
{brief['tags']}

[IMPORTANT]
• No lyrics — instrumental only
• No sudden tempo changes unless noted above
• Fade out naturally in the final 3 seconds
• Optimised for {brief['platform']} — keep overall energy controlled, not overwhelming
• The music supports the visuals — it should not compete with them"""


def build_udio_prompt(brief: dict) -> str:
    dur_secs = music_seconds(brief)
    cues_condensed = " | ".join(brief["timeline_cues"][:6])
    return f"""Instrumental {brief['style']}, {brief['bpm_range']}, {brief['tags']}.

Mood arc: {brief['mood_arc']}.
Instrumentation: {brief['instruments']}.
Timeline: {cues_condensed}.

{dur_secs} seconds total. No lyrics. No vocals. Fade out last 3 seconds.
Optimised for {brief['platform']} short-form video background music.
Music energy level: moderate — supportive, not dominant."""


def build_stable_audio_prompt(brief: dict) -> str:
    dur_secs = music_seconds(brief)
    return f"""{brief['style']}, {brief['tags']}, {brief['bpm_range']},
{brief['instruments']},
mood progression: {brief['mood_arc']},
{dur_secs} seconds, instrumental, no vocals, cinematic quality,
fade out at end, background music for {brief['platform']} video,
professional audio production quality"""


def build_soundraw_prompt(brief: dict) -> str:
    dur_secs = music_seconds(brief)
    return f"""SOUNDRAW SETTINGS:
─────────────────────────────────────
Genre:      Cinematic / Ambient
Mood:       {brief['mood_arc'].split('→')[0].strip().title()} → {brief['mood_arc'].split('→')[-1].strip().title()}
Tempo:      {brief['bpm_range']}
Length:     {dur_secs} seconds
Energy:     Start LOW → End {'HIGH' if 'cta' in brief['arc_type'] else 'MEDIUM'}
Instruments:{brief['instruments']}
─────────────────────────────────────
MANUAL NOTES:
• Use the timeline editor to match energy to arc:
  - First {int(dur_secs * 0.33)}s: low energy, minimal arrangement
  - Middle {int(dur_secs * 0.33)}s: building, add instruments
  - Final {int(dur_secs * 0.34)}s: peak energy, full arrangement
• Export as WAV or MP3 at 44100Hz stereo"""


def build_generic_prompt(brief: dict) -> str:
    dur_secs = music_seconds(brief)
    mins, secs = divmod(dur_secs, 60)
    cues_str = "\n".join(f"  {c}" for c in brief["timeline_cues"])
    return f"""BGM MUSIC BRIEF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Project     : {brief['project_name']}
Platform    : {brief['platform']}
Duration    : {mins}:{secs:02d} ({dur_secs} seconds exactly)
Audio Mode  : {brief['audio_mode']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STYLE
{brief['style']}

MOOD ARC
{brief['mood_arc']}

INSTRUMENTATION
{brief['instruments']}

TEMPO
{brief['bpm_range']} — {brief['tempo_feel']}

TAGS / GENRE
{brief['tags']}

TIMELINE
{cues_str}

TECHNICAL REQUIREMENTS
• Format    : MP3 (192kbps minimum) or WAV (44100Hz stereo)
• Duration  : Must be exactly {dur_secs} seconds OR longer (will be trimmed in pipeline)
• Vocals    : NONE — instrumental only
• Fade out  : Over final 3 seconds
• Mix level : any — the pipeline normalises the final mix to target_lufs (−14 LUFS) and ducks music under the VO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After generating: Save as bgm.mp3 (or bgm.wav) and paste the full
file path into the "bgm_path" field in Project Settings sheet of story_plan.xlsx"""


def build_gemini_prompt(brief: dict) -> str:
    """
    Single natural-language paragraph for Gemini → Lyria 3 "Describe your track" box.
    Gemini does NOT use structured tags — it reads prose description and infers everything.
    The prompt embeds:
      • Emotional journey (arc type → mood progression)
      • Instrument palette and texture
      • Tempo feel derived from shot cut rhythm
      • Key moment triggers (pivot, CTA, breath)
      • Platform context and mix guidance
      • Duration + fade out instruction
    """
    dur_secs   = music_seconds(brief)
    mins, secs = divmod(dur_secs, 60)
    dur_str    = f"{mins} minute{'s' if mins != 1 else ''} and {secs} seconds" if mins else f"{secs} seconds"

    # Translate arc type into a human story for Gemini to feel
    arc_story = {
        "problem_to_solution": (
            "The music begins in a place of quiet struggle — sparse, heavy, and uncertain, "
            "like someone carrying a weight they haven't named yet. "
            "As the story unfolds, a single instrument starts to push through the heaviness — "
            "a piano note, a string phrase — suggesting that something is about to change. "
            "By the final third, the music opens up fully: warm, resolved, and forward-moving, "
            "as if the person finally found the answer they were looking for."
        ),
        "aspiration_build": (
            "The music opens with warmth and quiet possibility — hopeful from the very first note, "
            "like the feeling of starting something you believe in. "
            "It builds steadily and naturally, adding layers of energy and brightness, "
            "until it arrives at a fully uplifting, confident resolution that feels earned, not forced."
        ),
        "sustained_tension": (
            "The music holds a sustained low-energy tension throughout — minimal, atmospheric, "
            "and slightly unsettling, like a question that hasn't been answered yet. "
            "Instruments are sparse: low strings, a faint bass pulse, occasional piano. "
            "The release, when it comes, should feel sudden and complete."
        ),
        "call_to_action": (
            "The music is confident and bright from the start — clean, commercial energy "
            "that communicates 'this is the moment to act.' "
            "It stays elevated and forward-moving throughout, with a strong, "
            "satisfying close that doesn't fade into uncertainty."
        ),
        "neutral_journey": (
            "The music is warm and steady throughout — like background light in a room, "
            "present but never demanding attention. "
            "Natural, acoustic, authentic. It supports the visuals without competing."
        ),
    }.get(brief["arc_type"], "The music is warm and cinematic, supporting the visual story throughout.")

    # Build key moment sentence if any exist
    moment_phrases = []
    for km in brief.get("key_moments", []):
        t = km.get("time", 0)
        if km["type"] == "pivot_moment":
            moment_phrases.append(
                f"At around {t} seconds, the music should noticeably brighten — "
                f"a key change or a new instrument entering — marking the emotional turning point."
            )
        elif km["type"] == "cta_moment":
            moment_phrases.append(
                f"At around {t} seconds, the music reaches its peak energy "
                f"and stays there through to the end."
            )
        elif km["type"] == "breath_moment":
            moment_phrases.append(
                f"At around {t} seconds, allow a very brief musical breath — "
                f"a half-beat of space — before continuing."
            )
    moments_str = " ".join(moment_phrases) if moment_phrases else ""

    # Instrument and tempo in natural language
    instrument_natural = brief["instruments"].replace(",", " —").strip(". ")
    tempo_natural = brief["bpm_range"].replace("–", " to ")

    return (
        f"Create an instrumental background music track for a {brief['platform']} video. "
        f"The video is {dur_str} long — the music should match this duration exactly and "
        f"fade out naturally over the final 3 seconds. No lyrics, no vocals, purely instrumental. "
        f"\n\n"
        f"{arc_story} "
        f"\n\n"
        f"The instrumentation should include {instrument_natural}. "
        f"Tempo around {tempo_natural} — {brief['tempo_feel']}. "
        f"{moments_str} "
        f"\n\n"
        f"The overall mood progression is: {brief['mood_arc']}. "
        f"Style: {brief['style']}. "
        f"The music should feel supportive, not dominant — it sits behind the visuals "
        f"and carries the emotional weight without overpowering them. "
        f"Mix at a moderate level, suitable for background use with text overlays on screen. "
        f"Genre reference: {brief['tags']}."
    ).strip()


TOOL_BUILDERS = {
    "gemini":       ("Google Gemini (Lyria 3)", "gemini.google.com/app", build_gemini_prompt),
    "suno":         ("Suno AI",                 "suno.com",              build_suno_prompt),
    "udio":         ("Udio",                    "udio.com",              build_udio_prompt),
    "stable_audio": ("Stable Audio",            "stability.ai",          build_stable_audio_prompt),
    "soundraw":     ("Soundraw",                "soundraw.io",           build_soundraw_prompt),
    "generic":      ("Generic / Other",         "",                      build_generic_prompt),
}


# ── HTML report ───────────────────────────────────────────────────────────────

def build_html(brief: dict, prompts: dict, project_name: str) -> str:
    arc_label = {
        "problem_to_solution": "🎭 Problem → Solution Arc",
        "aspiration_build":    "🌅 Aspiration Build Arc",
        "sustained_tension":   "⚡ Sustained Tension Arc",
        "call_to_action":      "🚀 Call to Action Arc",
        "neutral_journey":     "🌿 Neutral Journey Arc",
    }.get(brief["arc_type"], brief["arc_type"])

    cues_html = "".join(
        f'<li class="{"key-moment" if "⚑" in c else ""}">{html.escape(c)}</li>'
        for c in brief["timeline_cues"]
    )

    tool_cards = ""
    for tool_key, (tool_name, tool_url, _) in TOOL_BUILDERS.items():
        if tool_key not in prompts:
            continue
        prompt_text = prompts[tool_key]
        card_id = f"prompt_{tool_key}"
        url_html = f'<a href="https://{tool_url}" class="tool-url" target="_blank">{tool_url} ↗</a>' if tool_url else ""

        # Tool-specific usage instruction
        usage_note = {
            "gemini": (
                "🔵 <strong>Gemini (Lyria 3):</strong> Go to "
                "<a href='https://gemini.google.com/app' target='_blank' style='color:#4285F4'>gemini.google.com/app</a> "
                "→ click the <strong>🎵 Music</strong> icon at the bottom → paste this entire prompt into "
                "the <em>'Describe your track'</em> box → click generate. "
                "Download the result and save as <code>bgm.mp3</code>."
            ),
            "suno": "Go to suno.com → Create → paste into the prompt box. Enable Instrumental mode.",
            "udio": "Go to udio.com → paste into the prompt field. Select duration closest to your video length.",
            "stable_audio": "Go to stability.ai → Stable Audio → paste prompt. Set duration in seconds.",
            "soundraw": "Go to soundraw.io → use the settings panel values shown in the prompt below.",
            "generic":  "Paste this brief into any music AI tool or share with a composer.",
        }.get(tool_key, "")

        tool_cards += f"""
        <div class="tool-card {'gemini-card' if tool_key == 'gemini' else ''}">
          <div class="tool-header">
            <span class="tool-name">{'🔵 ' if tool_key == 'gemini' else ''}{html.escape(tool_name)}</span>
            {url_html}
            <button onclick="copyText('{card_id}', this)" class="copy-btn">📋 Copy Prompt</button>
          </div>
          <div class="usage-note">{usage_note}</div>
          <textarea id="{card_id}" readonly onclick="this.select()">{html.escape(prompt_text)}</textarea>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BGM Prompt — {html.escape(project_name)}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#0f0f17; color:#e8e4dc; padding:24px; line-height:1.6; }}
  .header {{ background:linear-gradient(135deg,#1a1a2e,#16213e);
             border:1px solid #DE7D14; border-radius:12px;
             padding:24px 30px; margin-bottom:24px; }}
  .header h1 {{ color:#DE7D14; font-size:22px; margin-bottom:6px; }}
  .header p  {{ color:#888; font-size:13px; }}
  .meta-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
                gap:12px; margin-bottom:24px; }}
  .meta-card {{ background:#1a1a2e; border:1px solid #2a2a40;
                border-radius:10px; padding:14px 18px; }}
  .meta-card .label {{ font-size:10px; color:#888; text-transform:uppercase;
                       letter-spacing:1px; margin-bottom:4px; }}
  .meta-card .value {{ font-size:15px; color:#DE7D14; font-weight:600; }}
  .section {{ background:#1a1a2e; border:1px solid #2a2a40;
              border-radius:12px; padding:20px 24px; margin-bottom:20px; }}
  .section h2 {{ font-size:16px; color:#DE7D14; margin-bottom:14px;
                 border-bottom:1px solid #2a2a40; padding-bottom:8px; }}
  .brief-row {{ display:flex; gap:12px; margin-bottom:8px; font-size:13px; }}
  .brief-label {{ color:#888; min-width:120px; flex-shrink:0; }}
  .brief-value {{ color:#e8e4dc; }}
  ul.timeline {{ list-style:none; margin-top:6px; }}
  ul.timeline li {{ font-size:13px; color:#c5b8f0; padding:4px 0;
                    border-bottom:1px solid #1f1f30; font-family:'Courier New',mono; }}
  ul.timeline li.key-moment {{ color:#DE7D14; }}
  .tool-card {{ background:#111120; border:1px solid #2a2a40;
                border-radius:10px; padding:18px 20px; margin-bottom:16px; }}
  .gemini-card {{ border-color:#4285F4; background:#0d1220; }}
  .usage-note {{ font-size:12px; color:#aaa; margin-bottom:10px;
                 background:#0f0f17; border-radius:6px; padding:8px 12px;
                 border-left:3px solid #DE7D14; line-height:1.6; }}
  .gemini-card .usage-note {{ border-left-color:#4285F4; }}
  .gemini-card .tool-name {{ color:#4285F4; }}
  .tool-header {{ display:flex; align-items:center; gap:12px;
                  flex-wrap:wrap; margin-bottom:12px; }}
  .tool-name  {{ font-size:15px; font-weight:700; color:#DE7D14; }}
  .tool-url   {{ font-size:12px; color:#6708C0; text-decoration:none; }}
  .tool-url:hover {{ text-decoration:underline; }}
  .copy-btn   {{ margin-left:auto; background:#DE7D14; color:#fff; border:none;
                 padding:6px 14px; border-radius:6px; cursor:pointer;
                 font-size:12px; font-weight:600; }}
  .copy-btn:hover   {{ background:#c56d10; }}
  .copy-btn.copied  {{ background:#27ae60; }}
  textarea {{ width:100%; background:#0f0f17; border:1px solid #333;
              border-radius:8px; color:#e8e4dc; padding:14px;
              font-size:12px; line-height:1.6; resize:vertical; min-height:200px;
              font-family:'Courier New',monospace; cursor:pointer; }}
  textarea:focus {{ outline:none; border-color:#DE7D14; }}
  .save-note {{ background:#0a1a0a; border:1px solid #27ae60;
                border-radius:8px; padding:16px 20px; margin-top:20px;
                font-size:13px; color:#c5f0c5; }}
  .save-note strong {{ color:#27ae60; }}
  code {{ background:#1e1e30; color:#DE7D14; padding:2px 6px;
          border-radius:3px; font-size:12px; }}
  .footer {{ text-align:center; color:#333; font-size:11px; margin-top:28px; }}
</style>
</head>
<body>

<div class="header">
  <h1>🎵 BGM Generation Brief</h1>
  <p>Project: <strong>{html.escape(project_name)}</strong> &nbsp;|&nbsp;
     Generated: {datetime.now().strftime("%d %b %Y %H:%M")}</p>
</div>

<div class="meta-grid">
  <div class="meta-card">
    <div class="label">Video Duration</div>
    <div class="value">{brief['total_duration']}s</div>
  </div>
  <div class="meta-card">
    <div class="label">Emotional Arc</div>
    <div class="value">{html.escape(arc_label)}</div>
  </div>
  <div class="meta-card">
    <div class="label">Mood Progression</div>
    <div class="value">{html.escape(brief['mood_arc'])}</div>
  </div>
  <div class="meta-card">
    <div class="label">Tempo Range</div>
    <div class="value">{html.escape(brief['bpm_range'])}</div>
  </div>
  <div class="meta-card">
    <div class="label">Platform</div>
    <div class="value">{html.escape(brief['platform'])}</div>
  </div>
  <div class="meta-card">
    <div class="label">Audio Mode</div>
    <div class="value">{html.escape(brief['audio_mode'])}</div>
  </div>
</div>

<div class="section">
  <h2>Music Brief Summary</h2>
  <div class="brief-row"><span class="brief-label">Style</span>
    <span class="brief-value">{html.escape(brief['style'])}</span></div>
  <div class="brief-row"><span class="brief-label">Instruments</span>
    <span class="brief-value">{html.escape(brief['instruments'])}</span></div>
  <div class="brief-row"><span class="brief-label">Cut Energy</span>
    <span class="brief-value">{html.escape(brief['tempo_feel'])}</span></div>
  <div class="brief-row"><span class="brief-label">Genre Tags</span>
    <span class="brief-value">{html.escape(brief['tags'])}</span></div>
</div>

<div class="section">
  <h2>Timeline Cues &nbsp;<span style="font-size:11px;color:#888;font-weight:400">
    (⚑ = key musical moment)</span></h2>
  <ul class="timeline">{cues_html}</ul>
</div>

<div class="section">
  <h2>Copy-Paste Prompts by Tool</h2>
  {tool_cards}
</div>

<div class="save-note">
  <strong>After generating your BGM:</strong><br>
  1. Save the file as <code>bgm.mp3</code> (or <code>bgm.wav</code>) in your music folder<br>
  2. Open <code>story_plan.xlsx</code> → Project Settings sheet<br>
  3. Paste the full file path into the <code>bgm_path</code> row<br>
  4. Optional: adjust <code>bgm_volume</code> (default <code>0.4</code>; the VO ducks it automatically)<br>
  5. Continue the pipeline — <code>audio_mixer.py</code> will layer it automatically
</div>

<div class="footer">
  UniqBrio Story-to-Video Pipeline · bgm_prompt_generator.py
</div>

<script>
function copyText(id, btn) {{
  const el = document.getElementById(id);
  el.select();
  document.execCommand('copy');
  btn.textContent = '✅ Copied!';
  btn.classList.add('copied');
  setTimeout(() => {{
    btn.textContent = '📋 Copy Prompt';
    btn.classList.remove('copied');
  }}, 2500);
}}
</script>
</body>
</html>"""


# ── Excel updater ─────────────────────────────────────────────────────────────

def update_excel_bgm_note(xlsx_path: Path, brief: dict):
    """Adds a BGM brief note row to Project Settings sheet if bgm_path is empty."""
    try:
        import openpyxl
        wb   = openpyxl.load_workbook(xlsx_path)
        ws   = wb["Project Settings"]
        for row in ws.iter_rows(min_row=3):
            if row[0].value == "bgm_path" and not row[1].value:
                # Highlight the cell and leave the hint in the Notes column — never put prose in the
                # value cell: story_reader would read it as a file path.
                from openpyxl.styles import PatternFill
                row[1].fill = PatternFill("solid", fgColor="FDEBD0")
                if len(row) > 2 and not row[2].value:
                    row[2].value = f"Paste the generated BGM file path in the Value cell ({music_seconds(brief)}s track)"
        wb.save(xlsx_path)
    except Exception as e:
        print(f"  ⚠️  Could not update Excel: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate BGM creation prompts from project.json story arc"
    )
    parser.add_argument("project",     help="Path to project.json")
    parser.add_argument("--tool",      default="gemini",
                        choices=list(TOOL_BUILDERS.keys()),
                        help="Target music AI tool (default: gemini)")
    parser.add_argument("--all-tools", action="store_true",
                        help="Generate prompts for all supported tools")
    parser.add_argument("--out",       default=None,
                        help="Output folder (default: same as project.json)")
    args = parser.parse_args()

    proj_path = Path(args.project)
    if not proj_path.exists():
        sys.exit(f"❌  Not found: {proj_path}")

    project      = json.loads(proj_path.read_text(encoding="utf-8-sig"))
    shots        = project.get("shots", [])
    project_name = project.get("project_name", "My Video")
    out_dir      = Path(args.out) if args.out else proj_path.parent

    if not shots:
        sys.exit("❌  No shots found in project.json — run story_reader.py first")

    print(f"\n{'═'*62}")
    print(f"  BGM Prompt Generator  |  {len(shots)} shots")
    print(f"  Project: {project_name}")
    print(f"{'═'*62}\n")

    # Analyse arc
    analysis = analyse_arc(shots, project.get("timeline"))

    print(f"  Emotional arc   : {analysis['arc_type']}")
    print(f"  Total duration  : {analysis['total_duration']}s")
    print(f"  Avg shot length : {analysis['avg_shot_dur']}s")
    print(f"  BPM range       : {analysis['bpm_range']}")
    print(f"  Key moments     : {len(analysis['key_moments'])}")

    # Build brief
    brief = build_bgm_brief(analysis, project)

    # Build prompts
    tools_to_generate = list(TOOL_BUILDERS.keys()) if args.all_tools else [args.tool]
    prompts = {}
    for tool_key in tools_to_generate:
        _, _, builder = TOOL_BUILDERS[tool_key]
        prompts[tool_key] = builder(brief)
        tool_name = TOOL_BUILDERS[tool_key][0]
        print(f"  ✅  Prompt built for {tool_name}")

    # Write HTML
    html_path = out_dir / "bgm_prompts.html"
    html_path.write_text(build_html(brief, prompts, project_name), encoding="utf-8")
    print(f"\n  📄  HTML report  → {html_path}")

    # Write JSON brief
    json_path = out_dir / "bgm_brief.json"
    json_path.write_text(
        json.dumps({"brief": brief, "analysis": {k: v for k, v in analysis.items() if k != "segments"}},
                   indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"  📄  BGM brief    → {json_path}")

    # Update project.json
    project["bgm_brief"] = brief
    proj_path.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  📄  project.json updated with bgm_brief")

    # Update Excel if available
    xlsx_path = Path(project.get("source_xlsx", ""))
    if xlsx_path.exists():
        update_excel_bgm_note(xlsx_path, brief)
        print(f"  📄  Excel bgm_path row highlighted")

    print(f"\n{'═'*62}")
    print(f"  NEXT STEPS:")
    print(f"  1. Open bgm_prompts.html in your browser")
    print(f"  2. Copy the prompt for your preferred tool")
    print(f"  3. Generate the BGM in Suno / Udio / Soundraw / etc.")
    print(f"  4. Save as bgm.mp3 and paste path into Project Settings → bgm_path")
    print(f"  5. Continue pipeline — audio_mixer.py layers it automatically")
    print(f"{'═'*62}\n")


if __name__ == "__main__":
    main()
