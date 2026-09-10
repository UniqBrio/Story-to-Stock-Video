# Story-to-Stock-Video Operating System (SVOS) — v1
### Skill-routing workflow: voice input → script → curated stock assets → assembled, publish-ready video
*Companion to SCOS v2 (script creation) and the UniqBrio SDLC / Website workflows. This flow produces faceless stock-footage videos; for AI-generated (I2V) films use the AI Film Studio pipeline instead.*

---

## Part 0 — Skill Resolution Protocol (read first, applies everywhere)

Whenever this system names a skill, resolve it in this order:

1. **Claude cloud skills** (installed in claude.ai / available to Cowork) — use directly.
2. **Local skill library:** `C:\Explorations\Pull-skill-research-MultiLLMs\output` — load `<skill-name>.md` (flat file) or `<skill-name>\SKILL.md`.
3. **Neither exists** → invoke `skills-gap-detector`, log the gap in the Production Bible §9, and STOP at the current gate. Never silently improvise a missing specialist.

The full per-skill location map for this flow is in `Skill-Resolution-Map.md` (same folder). Two skills were created specifically for this flow and live in the local library: **`voice-input-intake-director`** and **`stock-footage-search-curation-director`**.

For automation, pass resolved skill paths explicitly in task prompts (scheduled runs must not depend on interactive discovery).

---

## Part 1 — Why v1 replaces the old pipeline

The previous scripts (asset_fetcher.py et al.) failed for three diagnosed reasons, and each maps to a new mandatory discipline:

| Old failure | New discipline | Owner |
|---|---|---|
| Blind keyword search, 1 download per shot, never visually checked | 5–10 candidates/shot, frames extracted and VIEWED, 6-axis scoring, contact-sheet approval | `stock-footage-search-curation-director` |
| Shots timed arbitrarily; VO squeezed to fit | VO Timing Map is authoritative; shots are built to voice | `voice-input-intake-director` |
| Clips from different shoots read as disjointed stock montage | Explicit montage-vs-continuity decision + unified grade + overlay-carried narrative | curation director + `finishing-colorist` + `text-on-screen-system` |

The Python assembly scripts (clip_normaliser, overlay_engine, audio_mixer, transition_engine, final_export, run_pipeline) are RETAINED as the render layer. What changes is everything that decides *what* they render.

**Render layer v2 (2026-09-05):** the scripts were rebuilt so they can actually render the five treatments (T1 cards, T4 product inserts, T5 logo card), time overlays from the real assembled timeline, duck music under the VO, normalise to −14 LUFS, respect Reels safe zones, render Tamil, apply one unified grade, and run an automated QA battery (`qa_check.py`) before G6. Shared code lives in `svos_common.py`; a synthetic end-to-end test is `selftest.py`. Full audit and settings: `RENDER-LAYER-v2-NOTES.md`.

---

## Part 2 — The 8-Stage Routing Map

**Effort weighting: Stages 0–3 get ~55% of total effort.** ✅ = cloud skill · 📁 = local library · ★ = created for this flow. Human gates 🚦 are never auto-passed.

### STAGE 0 — Voice intake
★`voice-input-intake-director` 📁 — Mode A (spoken idea) or Mode B (finished VO + text). Produces transcript/verified script, intent card, voice strategy, **VO Timing Map**.
`production-bible-manager` 📁 — open the per-video Production Bible (the handoff file for every later stage).
🚦 **G0:** founder confirms transcript, voice strategy, timing spot-check.

### STAGE 1 — Audience & psychology
`academy-owner-psychology-expert` ✅ · `content-psychology-agent` ✅ · `jobs-to-be-done-expert` ✅ · `parent-perspective-content-writer` ✅ (when relevant)
**Output:** Audience Card (segment, awareness, emotional now→desired, 3 secret questions, 1 objection, scroll-stop reason, Emotional Journey Map at 0/5/15/25s/end).
🚦 No writing until the card exists.

### STAGE 2 — Script (Mode A only; Mode B skips to Stage 3)
Concept-type selector routes to ONE format writer: `pain-point-reel-script-engine` / `feature-demo-script-writer` / `before-after-content-builder` / `academy-math-content-writer` / `testimonial-content-builder` / `objection-handling-content-writer` / `founder-origin-story-series-writer` / `mission-vision-content-writer` — all ✅.
Then: `video-hook-writing` ✅ (hook ≤1.5s) → `narrative-engine-master` 📁 (framework justification, retention hooks, peak-end) → `ai-generated-content-humanization-director` 📁 → `tamil-script-transcreation` ✅ (if Tamil version).
Specificity law: exact times, rupees, cities. ONE CTA.
🚦 **G2: script lock.** Then re-run ★`voice-input-intake-director` timing pass (record founder VO or direct TTS via `tts-voiceover-director` ✅ / `tamil-voiceover-tts-director` ✅) → locked VO Timing Map.

### STAGE 3 — Stock-aware storyboard
`storyboard-i2v` ✅ (shot-list method; ignore I2V prompt sections — targets are stock searches, not renders) with per-shot craft from `emotions` ✅ · `lighting-mood-color-tone` ✅ · `camera-angles-movements` ✅ (select/trim vocabulary, not generation).
Every shot row: scene_desc, emotional_beat + intensity (must build 2→5→8→3), duration FROM the VO Timing Map, india_context flag, negative cues, text overlay draft, transition intent (`transition-types-between-shots` ✅).
**Continuity decision:** montage mode (default) vs continuity-illusion mode — per ★curation director's rule.
**Producibility rule:** any shot unlikely to exist as stock (too specific, branded UI, named place) is rewritten now or reassigned to: app-screen capture (`product-ui-screen-integration-specialist` 📁), logo card, or kinetic-text shot (`kinetic-typography-design` ✅).
🚦 **G3: board lock.**

### STAGE 4 — Asset sourcing & curation ← the stage that failed before
★`stock-footage-search-curation-director` 📁 — 4–6 queries/shot across literal/emotion/metaphor/India lanes → 5–10 candidates/shot from Pexels+Pixabay(+Unsplash images) → frame extraction → 6-axis weighted scoring → shortlist → **contact sheet**.
`semantic-broll-interleaving-director` 📁 — extra cutaway suggestions where VO lines run long.
Failed pools → shot rewrite loop back to Stage 3 (one iteration max, then human).
🚦 **G4: founder approves the contact sheet** (2-minute review). Only then production-res downloads + license log.

### STAGE 5 — Assembly (the retained Python render layer)
Run in order against the updated project.json: `clip_normaliser.py` → assembly → `overlay_engine.py` → `audio_mixer.py` → `final_export.py`.
Feeding decisions from skills:
- `production-control-sheet-generator` ✅ — the Talent Thief sheet is the bridge between the board and the scripts.
- `clipchamp-effects-transitions-director` ✅ — transitions/grades/Ken Burns per shot (sparing-use discipline).
- `text-overlay-timing-director` ✅ + `tamil-text-overlay-typography` ✅ (Tamil renders) + `copy-and-text-overlay` ✅ — overlay text, in/out times from the VO Timing Map, restraint budget.
- `audio-strategy-video` ✅ + `bgm-placement-analyzer` ✅ (or `audio-department-master` 📁 as single orchestrator) — BGM brief (bgm_prompt_generator.py retained), duck map to VO, silence strategy, −16 LUFS, end-sting per `sonic-brand-identity` 📁.
- `finishing-colorist` 📁 — ONE unified grade across all stock clips (this is what makes mixed footage read as one film).

### STAGE 6 — QA
`raw-video-qa-analyzer` ✅ (timestamped report: hook strength, asset-narration alignment, pacing, artifacts) → `video-assembly-overlay-analyzer` ✅ (overlay-injection improvements) → `engagement-element-injector` ✅ (optional, B2B-credibility-safe).
Battery: hook clarity in 1.5s · sound-off comprehension · 3-second freeze test · brand linkage · single CTA · Redmi-phone speaker + screen viewing (sound OFF first).
🚦 **G6: ship / fix / kill.** Max one fix loop back to Stage 5 before human re-decision.

### STAGE 7 — Publish package
`thumbnail-strategy` ✅ (cover frame) · `caption-hashtag-cta-writer` ✅ (EN+Tamil captions, hashtags, Comment-KEYWORD CTA) · `comment-to-dm-funnel-designer` ✅ · `instagram-posting-time-format-optimizer` ✅ (IST windows) · `platform-versioning-publisher` 📁 (per-platform re-edits; cross-post: IG → FB +2h → WhatsApp Status same evening → YT Shorts next morning → LinkedIn next business day) · `content-calendar-architect` ✅ (slot) · `performance-prediction-ab-planner` 📁 (hypotheses + 2–3 A/B tests).

**Stage 7a — Title & Caption Generation (mandatory, always fires, never asked for):** the instant G6 passes, `caption-hashtag-cta-writer` runs automatically and produces: (1) video title — 1 primary + 2 alt options, (2) Instagram Reel caption — hook, body, save/share trigger, Comment-[KEYWORD] CTA, 10–15 ICP hashtags, (3) Facebook post text — same story, 3–5 hashtags, same CTA keyword, (4) Tamil/Tanglish transcreation of both. Saved as `<video-id>_CAPTION_PACKAGE.md` next to the final export and delivered alongside it — on every topic, without the user asking.

### STAGE 8 — Learn (weekly)
`kpi-benchmark-diagnostic` ✅ validates Stage-7 hypotheses · `rejection-log-taste-memory` 📁 (killed clips WITH reasons — this compounds into better queries) · `content-repurposing-engine` ✅ · query-lane effectiveness review (which search lanes produced winners → update the curation director's lane weighting notes in the Production Bible).

---

## Part 3 — Paste-Ready Project Instructions

> Claude Project / Cowork task **"UniqBrio Stock Video Studio"**. Attach: this file, `Production-Bible-TEMPLATE.md`, `Skill-Resolution-Map.md`, `Stage-Run-Cards.md`. Paste as instructions:

```
You are the UniqBrio Stock Video Studio, running the SVOS v1 pipeline
(Story-to-Stock-Video-Operating-System.md in project knowledge). You produce
faceless stock-footage videos timed to the founder's voice.

SKILL RESOLUTION: 1) cloud skill if installed; 2) else load
C:\Explorations\Pull-skill-research-MultiLLMs\output\<skill-name>.md;
3) else run skills-gap-detector, log to Production Bible §9, and STOP at the
current gate. Name skills applied at each stage and their contribution.

HARD LAWS:
- The VO Timing Map is authoritative: shots fit the voice, never the reverse.
- No stock clip is selected without visual inspection + 6-axis scoring +
  contact-sheet approval (stock-footage-search-curation-director).
- One unified grade across all clips; overlay restraint budget; single CTA.
- Human gates G0, G2, G3, G4, G6 are never auto-passed.
- Tamil versions get their own transcreation AND their own timing map.

Run stages 0–8 per the routing map, writing every output into the Production
Bible file (append, never rewrite locked sections). End every run with the
skills-applied accounting per stage.
```

---

## Part 4 — Automation Notes (Cowork + scheduled tasks)

1. **One stage = one automatable task**; each reads the Production Bible fresh, applies named skills with explicit local paths, writes its section, STOPS at its gate. Gates become notify-and-wait, never skipped.
2. **The Production Bible file is the handoff medium** — not chat memory. Resumable, auditable.
3. **Stage 4 automation shape:** script fetches candidates + extracts frames → Claude (vision) scores per the rubric → contact sheet generated → founder notified → on approval, production downloads proceed. The visual-scoring step is the non-negotiable difference from asset_fetcher v1.
4. **Render layer:** the retained Python scripts run headless after G4; QA (Stage 6) reviews the exported file before G6.
5. **Fail-safe:** missing skill or empty candidate pool → log and pause, never improvise.

---

## Appendix — File map of this flow

| File | Role |
|---|---|
| `Story-to-Stock-Video-Operating-System.md` | This master doc |
| `Stage-Run-Cards.md` | Per-stage automatable prompts (copy-paste per task) |
| `Production-Bible-TEMPLATE.md` | Per-video working file template |
| `Skill-Resolution-Map.md` | Every skill → cloud / local path / new |
| `..\Pull-skill-research-MultiLLMs\output\voice-input-intake-director.md` | ★ new skill |
| `..\Pull-skill-research-MultiLLMs\output\stock-footage-search-curation-director.md` | ★ new skill |
| Existing `.py` scripts + `project.json` + `story_plan.xlsx` | Retained render layer |
