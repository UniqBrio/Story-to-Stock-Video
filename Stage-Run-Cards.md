# SVOS v1 — Stage Run Cards
One card = one automatable Cowork/Claude task. Each card reads the Production Bible fresh, applies the named skills (resolve per SVOS Part 0), writes its section, and STOPS at its gate.

---

## CARD 0 — Voice Intake
**Skills:** voice-input-intake-director (local) · production-bible-manager (local)
**Prompt:**
```
Read the Production Bible at <bible-path> (create from Production-Bible-TEMPLATE.md if absent).
Input: <audio file path and/or VO text>.
Apply C:\Explorations\Pull-skill-research-MultiLLMs\output\voice-input-intake-director.md.
Determine Mode A (idea dictation) or Mode B (finished VO + text).
Produce: transcript or verified script, intent card (Mode A), voice strategy record,
VO Timing Map (line_id | text EN | text TA | start | end | dur | breath_gap | emphasis | intensity).
Write all to Bible §1. STOP and request G0 approval; list exactly what the founder must confirm.
```

## CARD 1 — Audience Card
**Skills:** academy-owner-psychology-expert · content-psychology-agent · jobs-to-be-done-expert
**Prompt:**
```
Read Bible §1. Produce the Audience Card: segment + awareness stage; emotional state
now → desired; 3 questions they secretly have; 1 objection to pre-handle; scroll-stop
reason; Emotional Journey Map (0s/5s/15s/25s/end); psychological principles chosen and why.
Write to Bible §2. No script work in this task.
```

## CARD 2 — Script (Mode A only)
**Skills:** ONE format writer (per intent card) + video-hook-writing + narrative-engine-master (local) + ai-generated-content-humanization-director (local) + tamil-script-transcreation (if TA)
**Prompt:**
```
Read Bible §1–2. Route to the single format writer matching the intent card. Write the
script: hook ≤1.5s, retention hooks, curiosity loops, specificity law (exact rupees/times/
city), peak-end ending, ONE CTA, line-by-line VO. Humanization pass. Tamil transcreation
if required (separate timing!). Write to Bible §3. STOP for G2 script lock.
After lock: rerun voice-input-intake-director timing pass on the recorded/TTS VO and
update the VO Timing Map in Bible §1.
```

## CARD 3 — Stock-Aware Storyboard
**Skills:** storyboard-i2v (shot-list method only) · emotions · lighting-mood-color-tone · camera-angles-movements · transition-types-between-shots · kinetic-typography-design (fallback shots)
**Prompt:**
```
Read Bible §1–3. Build the shot list, one row per shot: shot_id, scene_desc,
emotional_beat + intensity (must build 2→5→8→3), duration FROM the VO Timing Map
(never invent durations), india_context (strict/preferred/neutral), negative_cues,
text_overlay draft, transition intent. Declare continuity strategy: montage (default)
or continuity-illusion. Rewrite any shot unlikely to exist as stock into: app-screen
capture, logo card, or kinetic-text shot. Write to Bible §4. STOP for G3 board lock.
```

## CARD 4 — Asset Sourcing & Curation
**Skills:** stock-footage-search-curation-director (local) · semantic-broll-interleaving-director (local)
**Prompt:**
```
Read Bible §4. Apply
C:\Explorations\Pull-skill-research-MultiLLMs\output\stock-footage-search-curation-director.md
in full: 4–6 queries per shot across literal/emotion/metaphor/India lanes; fetch 5–10
candidates per shot (Pexels videos, Pixabay videos, then images); extract 3 frames per
candidate and VISUALLY score on the 6-axis rubric; shortlist ≥70% with no axis <4.
Failed pools: one query-widening rerun, then flag the shot back to Card 3.
Produce contact_sheet.html with thumbnails, scores, license notes, director's picks.
Write candidate table to Bible §5. STOP for G4 contact-sheet approval.
After approval: download production-resolution files, update project.json per shot
(local_file, source, license, trim window), log rejections.

Render-layer commands:
  python run_pipeline.py story_plan.xlsx --contact-sheet 8      # candidates.json + contact_sheet.html, no downloads
  (founder picks in the browser → "Save picks.json"; duplicate picks are flagged)
  python run_pipeline.py story_plan.xlsx --apply-picks picks.json   # downloads only the approved picks, logs licenses, renders
  python validation_report.py project.json                          # Keep / Swap per shot → "Save review.json"
  python run_pipeline.py story_plan.xlsx --apply-review review.json # re-fetches swapped shots; rejected assets never return
  python content_safety.py --setup                                  # once: local U-rated safety models
  (the asset safety gate runs before Phase 3; flagged items → Output/safety/assets_report.html → safety_review.json)
  python run_pipeline.py story_plan.xlsx --apply-safety-review safety_review.json --from 3
```

## CARD 5 — Assembly
**Skills:** production-control-sheet-generator · clipchamp-effects-transitions-director · text-overlay-timing-director · tamil-text-overlay-typography (TA) · audio-department-master (local) or audio-strategy-video + bgm-placement-analyzer · finishing-colorist (local) · sonic-brand-identity (local)
**Prompt:**
```
Read Bible §1–5 + project.json. Fill the Talent Thief production control sheet.
Assign transitions/grade/Ken Burns per shot (sparing). Set overlay text + in/out times
from the VO Timing Map within the restraint budget (Tamil per-line drawtext rules if TA).
Produce the BGM brief (bgm_prompt_generator.py input), duck map to VO, −16 LUFS target,
end-sting. Specify ONE unified grade across all clips. Write every decision INTO THE
SHEET (Treatment, Text Style/Anim/Color, Card BG, Logo Bug, unified_grade, logo_mode,
duck_db, target_lufs, sting_path) — the render layer reads only the sheet. Then run:
  python run_pipeline.py story_plan.xlsx --from 3 --force
(phases 3–7 + automated QA). Paste the overlay manifest + duck map from
Output/render_manifest.json into Bible §6.
```

## CARD 6 — QA
**Skills:** raw-video-qa-analyzer · video-assembly-overlay-analyzer · engagement-element-injector (optional)
**Prompt:**
```
Read Output/qa_report.md (python qa_check.py project.json — spec, loudness, black/frozen
frames, dead air, hook window, restraint budget, single CTA, brand linkage) and the
thumb-stop frames in Output/qa/. Then WATCH the exported file. Produce the timestamped
QA report: hook clarity in 1.5s,
asset↔narration alignment per shot, pacing, artifacts, watch-time risks, sound-off
comprehension, 3-second freeze test, brand linkage, single-CTA check. Verdict:
SHIP / FIX (list) / KILL (reason). Max one fix loop to Card 5. Write to Bible §7.
STOP for G6 decision; remind the founder: phone viewing, sound OFF first.
```

## CARD 7 — Publish Package
**Skills:** thumbnail-strategy · caption-hashtag-cta-writer · comment-to-dm-funnel-designer · instagram-posting-time-format-optimizer · platform-versioning-publisher (local) · content-calendar-architect · performance-prediction-ab-planner (local)
**Prompt:**
```
Read Bible §1–7. Produce: cover-frame choice; EN+Tamil captions with scroll-stopping
first lines; hashtag sets; Comment-[KEYWORD] CTA + DM funnel; best day/time (IST
7–9 AM / 7–10 PM); per-platform versions and cross-post sequence (IG → FB +2h →
WhatsApp Status same evening → YT Shorts next morning → LinkedIn next business day);
first-hour engagement plan; performance hypotheses + 2–3 A/B tests. Write to Bible §8.
```

## CARD 8 — Learn (weekly, scheduled)
**Skills:** kpi-benchmark-diagnostic · rejection-log-taste-memory (local) · content-repurposing-engine
**Prompt:**
```
Pull actuals for videos published ≥72h. Compare against Bible §8 hypotheses.
Log clip rejections + reasons; review which search lanes produced approved clips and
update lane-weighting notes in Bible §9. Propose repurposing. Feed learnings forward.
```
