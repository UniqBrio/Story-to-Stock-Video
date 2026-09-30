# README — How Your Video Gets Generated (SVOS v1.1)
*Story-to-Stock-Video pipeline, updated with the three visual-treatment rules (blank frames · selective overlays · product-screen inserts) and the Demo Module Library.*

---

## What you give me

1. **Your speech transcript** (~1:23 of VO) — pasted in chat or as a file in this folder. If you have the recorded audio too, include it; your voice beats TTS.
2. **(Optional but recommended) The Demo Module Library** — see §6 for the exact format.

That's all. Everything below is what I do with it.

---

## §1 The five-treatment decision system

Every second of the video is assigned exactly ONE of five visual treatments, decided line-by-line from your VO Timing Map:

| # | Treatment | When it's chosen | Skill authority |
|---|---|---|---|
| T1 | **Blank text frame** (title card) | The line is a core message that must be remembered — a claim, a number, a turning point. Screen goes to a flat background; only the text speaks. | kinetic-typography-design · text-on-screen-system |
| T2 | **Stock visual, clean** | The line is narrative/emotional context; the footage carries it alone. | stock-footage-search-curation-director |
| T3 | **Stock visual + keyword overlay** | One word/phrase in the line deserves emphasis ("confidence") while the visual keeps playing. | text-overlay-timing-director · copy-and-text-overlay |
| T4 | **App screenshot / screen recording** | The script references the product, a feature, or a "how" — proof beats metaphor. Pulled from the Demo Module Library by timestamp. | product-ui-screen-integration-specialist · screen-content-compositing-specialist |
| T5 | **Logo / CTA card** | Open and/or close. | brand assets |

**Default is T2.** T1, T3, and T4 must be *earned* by the line's importance — never decorative.

## §2 The anti-clutter law (your priority, enforced)

- **Restraint budget for ~83s:** max **3 blank frames (T1)**, max **4–5 keyword overlays (T3)**, max **2–3 product inserts (T4)**. If the storyboard wants more, the weakest is cut.
- Never two treatment "events" back-to-back: a T1 card is always followed by at least ~6s of clean T2 before the next T1 or T3.
- One element on screen at a time: an overlay never appears during a product insert; a product insert never carries a keyword pop.
- Overlay text ≤ 4 words; card text ≤ 8 words; if it needs more words, it's a script problem, not a design problem.
- Blank-frame background: default warm off-white (#FAF7F2) with near-black text; dark charcoal with white text when the surrounding footage is dark or the message is grave; Brio Orange reserved for THE single most important frame (max once per video). No icons, no decorations on cards — text only, one subtle rise-in animation.

## §2b Brand logo placement & effects (decided from the script, at the storyboard gate)

The logo decision is made at Stage 3 alongside the treatments, using these rules (authorities: brand-film-codex 📁 · sonic-brand-identity 📁 · clipchamp-effects-transitions-director ✅ · viral-mechanics-social ✅):

| Script situation | Logo placement | Effect treatment |
|---|---|---|
| Default (pain story, story-led, curiosity hook) | **End only** (T5 close card, last 4–6s) | Logo rises/fades in AFTER the resolve line, paired with the audio end-sting; CTA text below. Never before the story lands. |
| Script opens with a bold claim or product-led promise ("here's how academies save ₹47k") | Small **corner logo bug** from 0:00 (8–12% width, ~90% opacity) + full end card | Bug appears with a 0.2s fade — static thereafter, no animation that competes with the hook. |
| Trust-critical content (testimonial, academy-math numbers) | Corner bug throughout + end card | Same restraint; the bug is a credibility signature, not decoration. |
| Never | **Opening logo splash / intro animation** | A 2–3s logo intro at 0:00 kills the hook window — hard ban on social formats. The first 1.5s belong to the hook, always. |

Effect grammar for the end card: one entrance (fade or gentle scale 0.96→1.0), brand colours per the codex, sting synced to the logo landing frame, CTA appears 0.4s after the logo settles. If the script's emotional close is soft/sentimental, the card inherits a slow 0.8s dissolve from the final shot; if the close is punchy, a hard cut to the card on the beat. One effect per element — never stacked.

This decision appears as its own row in the Stage-3 treatment table you approve at G3.

## §3 Pipeline (what happens, in order)

```
Your transcript (+ audio)
   │
   ▼
STAGE 0  Voice intake — voice-input-intake-director
         Clean transcript → VO Timing Map: every line with start/end/duration,
         emphasis words, intensity (1–10).                    🚦 G0: you approve
   │
   ▼
STAGE 3  Treatment-aware storyboard  (script already exists, so Stage 2 skips)
         Each VO line → shot row with a TREATMENT (T1–T5) + justification.
         T1 candidates = highest-intensity lines carrying the core message.
         T3 candidates = emphasis words from the Timing Map.
         T4 candidates = lines that mention product capability → matched
         against the Demo Module Library index by topic + timestamp.
         Budget check (§2) runs BEFORE you see it.            🚦 G3: you approve
         (You see a table: time | VO line | treatment | what's on screen | why)
   │
   ▼
STAGE 4  Asset gathering (only T2/T3 shots need stock)
         stock-footage-search-curation-director: 4–6 queries/shot,
         5–10 candidates, frames visually inspected, 6-axis scored
         → contact sheet with thumbnails.                     🚦 G4: you approve
         T4 shots: demo segments trimmed from library modules by transcript
         timestamp; screen framed per screen-content-compositing rules
         (device frame or full-bleed, cursor visible, 1–2s hold on the
         end state so the viewer registers the result).
         T1/T5 shots: rendered as text cards — no stock needed.
   │
   ▼
STAGE 5  Assembly (cloud render — ffmpeg + the retained Python layer)
         Normalise all clips to 1080×1920/30fps → cut to VO timing →
         burn overlays/cards (in/out times from the Timing Map; overlays
         enter ~0.15s after the word is spoken, exit at line end) →
         unified colour grade across all stock (finishing-colorist) →
         BGM + duck map under VO, −14 LUFS (`target_lufs`), end sting → export MP4.
         One command: `python run_pipeline.py story_plan.xlsx` — see §9.
   │
   ▼
STAGE 6  QA — raw-video-qa-analyzer battery: hook ≤1.5s, sound-off
         comprehension (overlays+cards must tell the story mute),
         3-second freeze test, clutter audit against §2, single CTA.
                                                              🚦 G6: ship/fix
   │
   ▼
STAGE 7  Delivery: final .mp4 committed to your folder + publish package
         (cover frame, captions EN/TA, hashtags, post time, cross-post plan).
```

Your total hands-on time: ~15 minutes across four gates.

## §4 How treatments are timed to your voice

The VO Timing Map is authoritative. Example for a 1:23 video:

| time | VO line | treatment | on screen |
|---|---|---|---|
| 0:00–0:04 | hook line | T2 | stock: the pain moment |
| 0:04–0:07 | key claim | **T1** | card: "₹47,000. Gone. Every year." |
| 0:07–0:18 | story develops | T2 | stock sequence, clean |
| 0:18–0:22 | "…be confident in whatever you do" | **T3** | stock + overlay: **CONFIDENCE** |
| 0:58–1:03 | "attendance in one tap" | **T4** | demo clip: attendance module, 0:58–1:03 segment from its library file |
| 1:18–1:23 | CTA | T5 | logo card + "DM 'BRIO'" |

## §5 What I need vs. what I decide

**You decide (at gates):** transcript fidelity, the storyboard treatment plan, stock picks from the contact sheet, ship/fix.
**I decide (and justify):** which lines earn T1/T3/T4, card colours, overlay words, stock selection shortlist, timing, grade, music placement — all reviewable, nothing final until G6.

## §6 Demo Module Library — the format that makes T4 automatic

One folder, e.g. `Demo-Modules\`, one module per product area:

```
Demo-Modules\
  attendance\
    attendance.mp4                ← screen recording of the full module demo
    attendance.transcript.md      ← what is said/shown, with timestamps
  fees\
    fees.mp4
    fees.transcript.md
  index.md                        ← one line per module: name, topics covered
```

`*.transcript.md` format (exactly your idea):

```
# attendance — demo transcript
recorded: 2026-08-01 · duration: 3:42 · resolution: 1920×1080

0:00–0:12  opening dashboard, navigate to Attendance
0:12–0:26  mark full class present in one tap
0:58–1:03  confidence point — "owners feel confident numbers are right"
1:04–1:31  monthly attendance report view
...
```

When your script touches a topic, I search `index.md` → open the module transcript → find the timestamp range → trim exactly that segment (+0.3s handles each side) → composite it per the screen-integration rules. Segments are never stretched: if a demo segment is longer than the VO line, I use the cleanest sub-window; if shorter, the surrounding stock absorbs the difference. Keep recordings at 1920×1080 or higher, cursor visible, no personal data on screen.

## §7 Skills applied (resolution: cloud → `Pull-skill-research-MultiLLMs\output` → stop)

voice-input-intake-director ★ · stock-footage-search-curation-director ★ · kinetic-typography-design ✅ · text-on-screen-system 📁 · text-overlay-timing-director ✅ · copy-and-text-overlay ✅ · product-ui-screen-integration-specialist 📁 · screen-content-compositing-specialist 📁 · semantic-broll-interleaving-director 📁 · feature-demo-script-writer ✅ (T4 line phrasing) · storyboard-i2v ✅ · transition-types-between-shots ✅ · clipchamp-effects-transitions-director ✅ · finishing-colorist 📁 · audio-department-master 📁 / bgm-placement-analyzer ✅ · sonic-brand-identity 📁 · tamil-script-transcreation / tamil-text-overlay-typography ✅ (TA version) · raw-video-qa-analyzer ✅ · thumbnail-strategy · caption-hashtag-cta-writer · instagram-posting-time-format-optimizer ✅.
All resolved; no gaps. (Full map: `Skill-Resolution-Map.md`.)

## §8 To start a video right now

Paste in chat: your transcript (+ mention if VO audio exists) and, if ready, the Demo-Modules folder path. Say **"Run SVOS Card 0."** First deliverable back to you: the VO Timing Map + proposed treatment plan for G0/G3 approval.

## §9 The render layer (v2, 2026-09-05)

The Python/FFmpeg layer now renders every treatment above from the sheet — see `RENDER-LAYER-v2-NOTES.md` for the full audit, settings and what each column does.

| Phase | Script | Renders |
|---|---|---|
| 1 | `story_reader.py` | Excel → `project.json`, validation + restraint audit (§2) |
| — | `preflight.py` | Keys, fonts (bundled `fonts/`), FFmpeg drawtext, VO/logo/product files — stops the run on critical gaps |
| 2 | `asset_fetcher.py` | Per-phrase stock search, scoring, license log; `--candidates N` → `contact_sheet.html` for G4 |
| 3 | `clip_normaliser.py` | T1 cards · T2/T3 stock (exact duration, arc grade + ONE unified grade, Ken Burns) · T4 product inserts · T5 logo card |
| 4 | `transition_engine.py` | Single-pass xfade assembly; writes the authoritative **timeline** |
| 5 | `overlay_engine.py` | Overlays timed from the timeline: styles, rise-in motion, safe zones, Tamil fonts, logo end-only/bug |
| 6 | `audio_mixer.py` | VO duck map, music/BGM, end sting, two-pass −14 LUFS |
| 7 | `final_export.py` | H.264 High · BT.709 · cover frame · `render_manifest.json` |
| 8 | `qa_check.py` | Automated half of the G6 battery → `Output/qa_report.md` + thumb-stop frames |

```
python run_pipeline.py story_plan.xlsx --preflight           # phase 1 + checks only
python run_pipeline.py story_plan.xlsx                       # full render + QA
python run_pipeline.py story_plan.xlsx --contact-sheet 8     # G4: candidates only, no downloads
python run_pipeline.py story_plan.xlsx --from 2 --apply-picks picks.json
python run_pipeline.py story_plan.xlsx --from 3 --force      # re-render, keep assets
python selftest.py                                           # synthetic end-to-end check
```

Sheet additions (run `python upgrade_story_plan.py story_plan.xlsx` on an old sheet): **Treatment** (T1–T5), **Text Style**, **Text Anim**, **Text Color**, **Card BG**, **Logo Bug**, **Font Size**, **VO Line**, **Notes**; settings for brand colours, `logo_mode` (default `end_only`, per §2b), `unified_grade`, `platform`/`safe_zones`, `target_lufs`, `duck_db`, `sting_path`, budgets.
