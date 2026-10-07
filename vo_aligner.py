#!/usr/bin/env python3
"""
vo_aligner.py  (SVOS v2 render layer — runs after preflight, before Phase 2)
─────────────
Fits a naturally recorded voiceover to the shot plan, so the user never has to
record to exact timings. The original VO file is never modified: this writes a
placement plan (project["vo_plan"]) that audio_mixer follows when it builds the
mix, cutting each phrase out of the cleared file and dropping it in place.

  1. Phrases   : speech segments of the VO (the same detection as the duck map)
  2. Words     : each phrase transcribed locally (Whisper, as in content_safety)
  3. Match     : phrases → shots IN ORDER (dynamic programming). Each shot is
                 described by its VO Line, else its on-screen text, else its scene
                 description; fuzzy word matching tolerates transcription slips
  4. Timing    : long pauses shortened to ≤ 0.35 s; each shot's phrases start at
                 that shot; a shot too short for its words is lengthened (never
                 shortened — the plan's pacing is kept)
  5. Decide    : confident → proceed and print the VO timing map.
                 Not confident → stop BEFORE anything is fetched or rendered and
                 ask one specific question (exit 3).

Answer a question by filling the sheet's VO Line column for the shots it names
(suggested lines are printed), or accept the plan as it is:
    python run_pipeline.py story_plan.xlsx --accept-vo

Usage:
    python vo_aligner.py project.json
    python vo_aligner.py project.json --accept      # proceed even when unsure

Exit codes: 0 aligned (or no voiceover) · 3 needs your answer · 1 error.
"""

from __future__ import annotations

import re
import sys
import argparse
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

from svos_common import (load_project, save_project, banner, output_dir, probe_duration, compute_timeline,
                         timeline_total, shot_kind, set_log)

LEAD, FIRST_LEAD, TAIL = 0.15, 0.10, 0.25   # breathing room around each shot's words (s)
MAX_GAP = 0.35                              # longest pause kept between phrases inside a shot
MIN_CONFIDENCE = 0.50                       # below this share of confidently matched shots → ask
MAX_GROWTH = 0.30                           # video may grow by 30 % to fit the words before we ask
STOP = {"a", "an", "the", "and", "or", "to", "of", "in", "on", "at", "for", "is", "are", "was", "it", "you", "your",
        "we", "our", "i", "my", "this", "that", "with", "by", "be", "so", "but", "if", "what", "dm"}


# ── Text matching ────────────────────────────────────────────────────────────
def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", (text or "").lower()) if w not in STOP]


def _hits(ref: str, heard: str) -> tuple[int, int]:
    """(key words of ref heard in the phrase group, key words in ref) — fuzzy, so 'chasing'≈'chase', 'uniq'≈'unique'."""
    rw, hw = set(words(ref)), set(words(heard))
    if not rw or not hw:
        return 0, len(rw)
    def heard_like(r):
        return any(SequenceMatcher(None, r, h).ratio() >= 0.75 or (len(r) >= 4 and h.startswith(r[:4])) for h in hw)
    return sum(1 for r in rw if heard_like(r)), len(rw)


def similarity(ref: str, heard: str, exact: bool = False) -> float:
    """0–1. A VO Line should be heard almost word for word (share of its words). On-screen text and scene
    descriptions only hint at the words, so two or three of their key words being heard is a full match."""
    hit, n = _hits(ref, heard)
    if not n:
        return 0.0
    return hit / n if exact else min(1.0, hit / min(n, 3))


def brand_words(project: dict) -> str:
    """Brand name for the logo card, from the logo's folder ('UniqBrio' → 'uniqbrio uniq brio')."""
    lp = project.get("logo", {}).get("path", "")
    name = Path(lp).parent.name if lp else ""
    parts = re.findall(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?![a-z])", name)
    return " ".join([name.lower()] + [x.lower() for x in parts]) if name else ""


def shot_reference(shot: dict, brand: str = "") -> tuple[str, str]:
    """(what the voiceover should say over this shot, where it came from). The VO Line wins; otherwise the
    on-screen text plus the scene description; the logo card also answers to the brand name."""
    if (shot.get("vo_line") or "").strip():
        return shot["vo_line"].strip(), "VO Line"
    ref = " ".join(x for x in ((shot.get("text_overlay") or "").strip(), (shot.get("scene_desc") or "").strip()) if x)
    if shot_kind(shot) == "logo_card" and brand:
        ref = f"{ref} {brand}".strip()
    return ref, ("on-screen text + scene" if ref else "")


# ── Phrases and words ────────────────────────────────────────────────────────
def phrases(vo: str) -> list[tuple[float, float]]:
    from audio_mixer import detect_speech                  # same speech map the duck map uses
    return detect_speech(vo, 0.0, probe_duration(vo))


def transcribe_phrases(project: dict, vo: str, segs: list) -> list[str]:
    try:
        import numpy as np
        import content_safety as cs
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", vo, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, timeout=600).stdout
        audio = np.frombuffer(raw, np.float32)
        lang = (project.get("safety", {}).get("vo_language") or "auto").lower()
        asr = cs.Engines(cs.models_dir(project)).asr("" if lang == "auto" else lang)
        out = []
        for a, b in segs:
            clip = audio[int(max(0.0, a - 0.15) * 16000):int((b + 0.15) * 16000)]
            out.append(asr.transcribe(clip) if clip.size > 1600 else "")
        return out
    except Exception as e:                                  # no models → align on timing alone, and say so
        print(f"  ⚠️  Could not transcribe the voiceover ({e.__class__.__name__}) — aligning on timing only")
        return [""] * len(segs)


# ── Alignment ────────────────────────────────────────────────────────────────
def speech_span(segs: list, i: int, j: int) -> float:
    """Length of phrases i..j-1 once pauses are shortened to MAX_GAP."""
    if j <= i:
        return 0.0
    talk = sum(b - a for a, b in segs[i:j])
    gaps = sum(min(MAX_GAP, segs[k + 1][0] - segs[k][1]) for k in range(i, j - 1))
    return talk + gaps


def room(shot: dict, first: bool) -> float:
    """Seconds of a shot available for words."""
    return max(0.3, float(shot.get("duration", 4.0)) - (FIRST_LEAD if first else LEAD) - TAIL
               - float(shot.get("trans_dur", 0) or 0))


def align(shots: list, segs: list, texts: list, brand: str = "") -> list[tuple[int, int]]:
    """Contiguous phrase range (i, j) per shot, in order, minimising mismatch + timing strain."""
    n, m, INF = len(segs), len(shots), float("inf")
    refs = [shot_reference(s, brand) for s in shots]

    def cost(k: int, i: int, j: int) -> float:
        if j == i:
            return 0.6                                        # a silent shot is allowed, but not free
        sp, av = speech_span(segs, i, j), room(shots[k], k == 0)
        timing = max(0.0, sp - av) / av + 0.3 * max(0.0, av - sp) / av
        ref, src = refs[k]
        return timing - 2.0 * similarity(ref, " ".join(texts[i:j]), exact=(src == "VO Line"))

    best = [[INF] * (n + 1) for _ in range(m + 1)]
    back = [[0] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for k in range(m):
        for i in range(n + 1):
            if best[k][i] == INF:
                continue
            for j in range(i, n + 1):
                c = best[k][i] + cost(k, i, j)
                if c < best[k + 1][j]:
                    best[k + 1][j], back[k + 1][j] = c, i
    ranges, j = [], n
    for k in range(m, 0, -1):
        i = back[k][j]
        ranges.append((i, j))
        j = i
    return ranges[::-1]


# ── Plan ─────────────────────────────────────────────────────────────────────
def build_plan(project: dict, vo: str, accept: bool) -> tuple[dict, list[str]]:
    shots = project["shots"]
    segs = phrases(vo)
    texts = transcribe_phrases(project, vo, segs)
    brand = brand_words(project)
    ranges = align(shots, segs, texts, brand)

    # lengthen shots that cannot hold their words; never shorten
    changes = []
    for k, (s, (i, j)) in enumerate(zip(shots, ranges)):
        s.setdefault("planned_duration", float(s.get("duration", 4.0)))
        need = speech_span(segs, i, j) + (FIRST_LEAD if k == 0 else LEAD) + TAIL + float(s.get("trans_dur", 0) or 0)
        new = max(float(s["planned_duration"]), round(need + 0.049, 1)) if j > i else float(s["planned_duration"])
        if new > float(s["planned_duration"]) + 1e-6:
            changes.append((s["shot_id"], float(s["planned_duration"]), new))
        s["duration"] = new
        if shot_kind(s) not in ("card", "logo_card"):
            s["trim_out"] = round(float(s.get("trim_in", 0) or 0) + new, 3)
    timeline = compute_timeline(shots)
    project["timeline"] = timeline

    placements, rows, matched, judged = [], [], 0, 0
    for k, (s, t, (i, j)) in enumerate(zip(shots, timeline, ranges)):
        heard = " ".join(x for x in texts[i:j] if x)
        ref, src = shot_reference(s, brand)
        sim = similarity(ref, heard, exact=(src == "VO Line")) if j > i else 0.0
        if j > i and ref:
            judged += 1
            matched += sim >= 0.30
        cursor = t["start"] + (FIRST_LEAD if k == 0 else LEAD)
        for p in range(i, j):
            a, b = segs[p]
            placements.append({"shot_id": s["shot_id"], "src_start": round(a, 3), "src_end": round(b, 3),
                               "at": round(cursor, 3), "text": texts[p]})
            cursor += (b - a) + (min(MAX_GAP, segs[p + 1][0] - b) if p + 1 < j else 0)
        rows.append({"shot_id": s["shot_id"], "start": t["start"], "end": t["end"], "heard": heard,
                     "reference": ref, "reference_from": src, "match": round(sim, 2), "phrases": j - i})

    planned_total = timeline_total(compute_timeline([dict(s, duration=s["planned_duration"]) for s in shots]))
    total = timeline_total(timeline)
    confidence = matched / judged if judged else 0.0
    questions = []
    weak = [r for r in rows if r["phrases"] and r["reference"] and r["match"] < 0.30]
    if confidence < MIN_CONFIDENCE and weak:
        questions.append(
            "I could not confidently tell which spoken words belong to which shot "
            f"({matched} of {judged} shots matched). Please fill the VO Line column for shots "
            f"{', '.join(r['shot_id'] for r in weak)} — suggested lines below — or accept this plan.")
    if planned_total and total > planned_total * (1 + MAX_GROWTH):
        questions.append(f"The voiceover needs {total:.1f}s but the plan is {planned_total:.1f}s "
                         f"(+{(total / planned_total - 1):.0%}). Shorten the voiceover, add shots, or accept the longer video.")
    silent_tail = [s["shot_id"] for s, (i, j) in zip(shots, ranges) if j == i and shot_kind(s) not in ("card", "logo_card")]
    plan = {"source": str(vo), "source_mtime": Path(vo).stat().st_mtime, "phrases": len(segs),
            "speech_seconds": round(sum(b - a for a, b in segs), 2), "vo_seconds": round(probe_duration(vo), 2),
            "placements": placements, "map": rows, "duration_changes": changes, "planned_total": round(planned_total, 2),
            "total": round(total, 2), "confidence": round(confidence, 2), "questions": questions,
            "accepted": bool(accept), "silent_shots": silent_tail}
    return plan, questions


def write_report(project: dict, proj_path: Path, plan: dict) -> Path:
    out = output_dir(project, proj_path) / "vo_timing.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    md = ["# VO timing map", "",
          f"Voiceover {Path(plan['source']).name}: {plan['vo_seconds']}s, of which {plan['speech_seconds']}s is speech "
          f"in {plan['phrases']} phrases. Video {plan['planned_total']}s planned → {plan['total']}s. "
          f"Confidence {plan['confidence']:.0%}.", "",
          "| Shot | Time | Heard | Expected (source) | Match |", "|---|---|---|---|---|"]
    for r in plan["map"]:
        md.append(f"| {r['shot_id']} | {r['start']:.1f}–{r['end']:.1f}s | {r['heard'] or '—'} | "
                  f"{r['reference'] or '—'} ({r['reference_from'] or 'none'}) | {r['match']:.0%} |")
    if plan["duration_changes"]:
        md += ["", "Lengthened to fit the words: " +
               ", ".join(f"{sid} {a:g}s → {b:g}s" for sid, a, b in plan["duration_changes"])]
    if plan["questions"]:
        md += ["", "## Questions", *[f"- {q}" for q in plan["questions"]], "",
               "Suggested VO Line per shot (paste into the sheet, correcting any mis-heard words):", ""]
        md += [f"- {r['shot_id']}: {r['heard']}" for r in plan["map"] if r["heard"]]
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="Fit the voiceover to the shot plan (phrase → shot alignment)")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--accept", action="store_true", help="Proceed with the plan even if it asks a question")
    args = ap.parse_args()

    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    vo = project.get("audio", {}).get("vo_path", "")
    if not vo or not Path(vo).exists():
        project.pop("vo_plan", None)
        save_project(project, proj_path)
        print("  ℹ️  No voiceover — nothing to align")
        return

    plan, questions = build_plan(project, vo, args.accept)
    if not any(r["heard"] for r in plan["map"]):
        # no words could be heard (tones, music, or no speech models): nothing to align on — and asking would
        # not help, so keep the voiceover exactly as recorded, from the start of the video
        project = load_project(proj_path)                     # undo the trial duration changes
        project.pop("vo_plan", None)
        save_project(project, proj_path)
        print("  ⚠️  No words could be heard in the voiceover — using it as recorded, from the start (no alignment)")
        return
    project["vo_plan"] = plan
    save_project(project, proj_path)
    report = write_report(project, proj_path, plan)

    banner("VO Aligner", f"{Path(vo).name}: {plan['vo_seconds']}s ({plan['speech_seconds']}s speech, "
                         f"{plan['phrases']} phrases) → video {plan['planned_total']}s → {plan['total']}s",
           f"confidence {plan['confidence']:.0%}")
    for r in plan["map"]:
        mark = "✅" if r["match"] >= 0.30 else ("·" if not r["phrases"] else "❔")
        print(f"  {mark} {r['shot_id']} {r['start']:5.1f}–{r['end']:5.1f}s  “{r['heard'] or '—'}”"
              f"   ⟵ {r['reference_from'] or 'no reference'}: {r['reference'][:40] or '—'}")
    for sid, a, b in plan["duration_changes"]:
        print(f"  ↔  {sid} lengthened {a:g}s → {b:g}s to fit its words")
    print(f"\n  📄  {report}")
    if questions and not args.accept:
        print("\n  ❔  I need your answer before rendering:")
        for q in questions:
            print(f"     • {q}")
        print("     Then rerun, or accept this plan with:  python run_pipeline.py <sheet> --accept-vo\n")
        sys.exit(3)
    print("\n  ✅  Voiceover aligned to the shots\n")


if __name__ == "__main__":
    main()
