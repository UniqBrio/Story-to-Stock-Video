# SVOS Render Layer v2 — Audit, Fixes & Operating Notes
*2026-09-05 · applies to the Python/FFmpeg render layer that Stage 5–6 of the SVOS pipeline runs. The decision stages (0–4, 7–8) are unchanged and still skill-driven.*

---

## 1. What was wrong (audit of v1)

The v1 scripts could not render most of what the SVOS README promises. Everything below was verified against the code and, where possible, by running it.

| # | Area | v1 defect | Effect on the video |
|---|---|---|---|
| 1 | Overlay timing | Overlays were timed from **planned** shot durations and ignored xfade overlaps | Every overlay after a dissolve/fade drifted later (0.5 s per transition) — text no longer landed on the spoken word |
| 2 | Audio mix | `amix` default `normalize=1` divides every input by N | With music + BGM + VO, the voice dropped ~10 dB — the worst possible failure for a VO-led reel |
| 3 | Audio | No ducking, no loudness normalisation, no end sting | Music fought the voice; level varied per video; Instagram re-normalised it unpredictably |
| 4 | Stock search | All keyword phrases were **joined into one query** (`"stressed teacher desk paperwork night frustrated admin work"`) | Over-specific query → empty result sets → `error` shots |
| 5 | Stock search | Highest-resolution rendition chosen regardless of orientation; no duration check; no de-dupe across shots | 4K landscape clips cropped to a sliver of portrait; clips shorter than the shot; the same clip in two shots |
| 6 | Logo card (T5) | Transparent logo PNG was scaled to **fill 1080×1920 and cropped** | A blown-up, cropped logo on black — not a brand end card |
| 7 | Logo | Corner logo burned across the **entire** video, including on top of the logo card | Double logo; violates README §2b (default is end-card only) |
| 8 | Treatments | No T1 blank text frame, no T4 product insert, no `treatment` field at all | The five-treatment system existed only on paper |
| 9 | Text positions | `bottom_center` at y = H−120, `top_center` at y = 120 | Inside Instagram's caption/actions band and header — text hidden behind the UI |
| 10 | Text fit | Fixed 72 px, no wrap, no measurement | Long lines ran off the frame; Tamil rendered as □ (Arial has no Tamil glyphs) |
| 11 | Motion | `enable=between()` only | Hard pop in/out; the README's "one subtle rise-in" was impossible |
| 12 | Grade | Per-shot grade only | No unified finishing grade — mixed stock read as a montage of strangers |
| 13 | Short sources | Clip shorter than the shot → shot silently shortened | Whole timeline shifted; VO out of sync |
| 14 | Assembly | Pairwise xfade re-encoding | N−1 generations of H.264 loss on the earliest shots |
| 15 | Export | No colour tags, no GOP control, 44.1 kHz | Slight colour shifts on some players; larger files than needed |
| 16 | Excel | Columns read by fixed index; shot IDs not normalised | Adding a column broke the reader; `1` vs `001` mismatches |
| 17 | API keys | Plain text in the sheet **and** committed in project.json | Pexels key is **dead (HTTP 403)**; Pixabay + Unsplash keys work |
| 18 | QA | None | No machine check before the human G6 gate |

## 2. What v2 does about it

```
story_plan.xlsx ─▶ story_reader ─▶ asset_fetcher ─▶ clip_normaliser ─▶ transition_engine
                   (validate,        (per-phrase        (cards · exact       (single-pass xfade,
                    restraint audit)   queries, scoring,   duration · grades ·   writes the TIMELINE)
                                       contact sheet)      product inserts)
                ─▶ overlay_engine ─▶ audio_mixer ─▶ final_export ─▶ qa_check
                   (timeline-timed,    (duck map, no       (BT.709, GOP,       (G6 battery →
                    styles, motion,     attenuation,        cover frame,        qa_report.md)
                    safe zones, Tamil)  −14 LUFS, sting)    manifest)
```

* **`svos_common.py`** — shared helpers: ffmpeg wrappers with a run log (`Output/logs/pipeline_ffmpeg.log`), font resolution (Latin + Tamil), PIL text measurement/auto-fit, safe zones, timeline maths, treatment resolution.
* **Timeline is authoritative.** `transition_engine` probes every normalised clip and writes `project.timeline` (start/end after overlaps). Overlays, the BGM brief and QA all read it.
* **Every clip is exactly its planned duration** (`tpad` holds the last frame if the source is short, with a warning).
* **Treatments:** `T1` flat colour card · `T2/T3` stock · `T4` product insert (letterboxed on a blurred copy, UI never cropped, end state held) · `T5` brand-colour logo card with a 0.5 s rise/fade logo entrance.
* **Overlays:** styles `caption / keyword / card / cta`, motion `rise / fade / pop / none`, PIL-measured wrap + shrink (no orphan words), 380 px legibility floor, Reels safe zones, Tamil → Noto Sans Tamil → Nirmala UI, one `drawtext` per line, one FFmpeg pass.
* **Logo modes:** `end_only` (default, README §2b) · `bug` · `both` · `none`, per-shot override; never on cards.
* **Audio:** `amix normalize=0`; VO speech map via `silencedetect` → music/BGM duck by `duck_db` with 150 ms attack / 350 ms release (map saved to `project.audio_report`); two-pass `loudnorm` to `target_lufs` (default −14 LUFS, TP −1.5 dBTP); sting lands 0.4 s before the end; 48 kHz.
* **Unified grade** (`unified_grade`: `warm_soft` default) applied to all stock after the per-shot arc grade; cards excluded. Optional fine grain.
* **Ken Burns on video** (slow 7 % push via time-driven crop) as well as on stills.
* **Fetcher:** per-phrase queries (≤ 6 lanes), orientation-aware rendition choice, duration-fit scoring, cross-shot de-dupe, license + author + page URL recorded to project.json and the Asset Tracker sheet; `--candidates N` writes `contact_sheet.html` for **G4** and downloads nothing until `--apply-picks picks.json`. Dead keys are reported once with the fix.
* **Export:** H.264 High@4.2, BT.709 tags, closed 2 s GOP, 14 Mbps cap, AAC 192k/48 kHz, faststart, `<name>_cover.jpg`, `render_manifest.json` (specs, measured loudness, timeline, overlays, licenses), `project.snapshot.json`.
* **QA (`qa_check.py`):** spec · loudness · black/frozen frames (cards excluded) · dead air · hook window · restraint budget · reading-time minimums · single CTA · brand linkage · safe zones · license log · thumb-stop frames at 0/1/2/3 s → `Output/qa_report.md`. Verdict is *READY FOR G6 REVIEW* or *FIX BEFORE G6* — the human gate is never auto-passed.
* **Self-test (`selftest.py`):** builds a 7-shot synthetic project (every treatment, Tamil, ₹, short source, product insert, VO bursts, sting) and asserts 19 checks. Passed on this machine in ~80 s.

## 3. Design elements — status after v2

| Element | v1 | v2 | Where it is set |
|---|---|---|---|
| T1 blank text frames | ✗ | ✅ off-white / charcoal / one orange | `Treatment=T1`, `Card BG`, `card_bg_default` |
| T3 keyword overlays | pop-in, unsafe zone | ✅ rise/fade, safe zone, ≤ 4 words audit | `Text Style=keyword`, `Text Anim` |
| T4 product inserts | ✗ | ✅ letterbox on blurred self, end-state hold, no text | `Treatment=T4`, `Local File Path` |
| T5 logo card | broken | ✅ purple card, logo entrance, CTA below | `Treatment=T5`, `logo_card_bg`, `logo_card_scale` |
| Corner logo bug | always on | ✅ end-only default; bug/both optional | `logo_mode`, `Logo Bug` |
| Overlay motion | ✗ | ✅ rise · fade · pop | `text_anim_default` |
| Safe zones | ✗ | ✅ Reels/Shorts/landscape | `platform`, `safe_zones` |
| Fonts | Arial only | ✅ Inter/Manrope/Poppins stack, Tamil stack | `text_font`, `fonts_folder`, `text_font_tamil` |
| Per-shot arc grade | ✅ | ✅ (kept) | `Color Grade` |
| Unified finishing grade | ✗ | ✅ warm_soft / clean_neutral / cool_calm | `unified_grade` |
| Film grain | ✗ | ✅ optional | `film_grain` |
| Ken Burns | stills only | ✅ stills + video push | `Ken Burns` |
| Transitions | ✅ (lossy) | ✅ single pass | `Transition Out`, `Trans Dur` |
| Ducking | ✗ | ✅ duck map | `duck_db` |
| Loudness | ✗ | ✅ −14 LUFS two-pass | `target_lufs`, `true_peak` |
| End sting | ✗ | ✅ | `sting_path`, `sting_volume` |
| Cover frame | ✗ | ✅ | `cover_time` |
| QA battery | ✗ | ✅ automated half | `qa_check.py` |

## 4. What I need from you (blocking the first real render)

1. **Pexels API key** — the one in the sheet returns HTTP 403. New key: <https://www.pexels.com/api/>. Prefer setting it as an environment variable (`PEXELS_API_KEY`) and blanking the sheet cell; the same works for `PIXABAY_API_KEY` / `UNSPLASH_API_KEY`. Treat the keys currently in `project.json` / `story_plan.xlsx` as leaked and rotate them.
2. **Voiceover file** — `vo_path` points to `C:\VO\voiceover.mp3`, which does not exist here. Without it there is no duck map and the video is music + captions only.
3. **Music** — `music_path` (`C:\Music\background.mp3`) does not exist; `bgm_path` (`Output\bgm.mp3`, 95 s) does and is used. Blank `music_path` if BGM is the only bed.
4. **Assets folder** — `C:\VideoProjects\Assets` does not exist; the six `downloaded` statuses in the sheet are stale. Run the fetcher (or `--contact-sheet 8` for the G4 flow) after fixing the key.
5. **Fonts (optional but recommended)** — drop `Inter-Bold.ttf` and `NotoSansTamil-Bold.ttf` into a `fonts\` folder next to the scripts (or set `fonts_folder`). Today the stack falls back to Arial Bold and Nirmala UI, which work but are not the brand fonts named in text-on-screen-system.
6. **End sting** — a 0.5–1.5 s audio logo file for `sting_path` (sonic-brand-identity §7). None exists in the repo.
7. **Demo Module Library** — for T4 shots, the screen-recording path goes in `Local File Path`; no library folder exists yet.
8. **Loudness target decision** — the README says −16 LUFS, the local `sonic-brand-identity` skill says −14 (Instagram's normalisation point). v2 defaults to −14; change `target_lufs` if you want −16.

## 5. Commands

```powershell
# one-shot render (assets already in place)
python run_pipeline.py story_plan.xlsx

# Stage-4 gate: gather 8 candidates per shot → contact_sheet.html, no downloads
python run_pipeline.py story_plan.xlsx --contact-sheet 8
#   … pick in the browser, save picks.json next to project.json …
python run_pipeline.py story_plan.xlsx --from 2 --apply-picks picks.json

# re-render after changing overlays / grades (keeps downloaded assets)
python run_pipeline.py story_plan.xlsx --from 3 --force

# re-download everything (approved picks are lost)
python run_pipeline.py story_plan.xlsx --refetch

# utilities
python upgrade_story_plan.py story_plan.xlsx     # add v2 columns/settings to an old sheet (backup first)
python validation_report.py project.json         # side-by-side asset review
python bgm_prompt_generator.py project.json      # BGM brief (uses the real timeline)
python qa_check.py project.json                  # QA battery only
python selftest.py                               # synthetic end-to-end test (~80 s)
```

Outputs land in `output_folder`: `final_video.mp4`, `final_video_cover.jpg`, `render_manifest.json`, `project.snapshot.json`, `qa_report.md/.json`, `qa/frame_0s…3s.jpg`, `logs/`.

## 6. New sheet fields (added by `upgrade_story_plan.py`)

**Shot Plan:** `Treatment` (T1–T5) · `Text Style` (auto/caption/keyword/card/cta) · `Text Anim` (rise/fade/pop/none) · `Text Color` (#hex) · `Card BG` (#hex) · `Logo Bug` (auto/yes/no) · `Font Size` (px) · `VO Line` · `Notes`. All optional; all have drop-downs. Columns are found by header name, so order does not matter.

**Project Settings:** `brand_color` · `card_bg_default` · `card_text_color` · `logo_card_bg` · `logo_card_scale` · `logo_mode` · `fonts_folder` · `text_font_tamil` · `text_anim_default` · `text_box` · `unified_grade` · `film_grain` · `platform` · `safe_zones` · `target_lufs` · `true_peak` · `duck_db` · `sting_path` · `sting_volume` · `vo_start` · `overlay_budget` · `card_budget` · `cover_time` · `max_duration`.

## 7. Skills the workflow depends on (verified against the local library 2026-09-05)

The render layer itself needs **no Claude skills** — it is Python + FFmpeg. The skills drive the decision stages. ★ = created for this flow · 📁 = present in `C:\Explorations\Pull-skill-research-MultiLLMs\output` · ☁ = cloud-only per the Skill Resolution Map, **not** in the local library (could not be verified from this session).

| Stage | Skill | What it does |
|---|---|---|
| 0 | voice-input-intake-director ★📁 | Turns founder voice / VO into a verified transcript, intent card and the VO Timing Map |
| 0 | production-bible-manager 📁 | Creates and governs the per-video Production Bible (locked handoff file) |
| 1 | academy-owner-psychology-expert 📁 | Behavioural manual for Indian arts/sports academy owners |
| 1 | content-psychology-agent 📁 | Persuasion principles applied to content |
| 1 | jobs-to-be-done-expert 📁 | Uncovers the real progress customers want |
| 1 | parent-perspective-content-writer 📁 | Secondary-buyer (parent) angle |
| 2 | pain-point-reel-script-engine 📁 · academy-math-content-writer 📁 · testimonial-content-builder 📁 · founder-origin-story-series-writer 📁 · mission-vision-content-writer 📁 | Format writers (one per video) |
| 2 | feature-demo-script-writer ☁ · before-after-content-builder ☁ · objection-handling-content-writer ☁ | Format writers, cloud only |
| 2 | video-hook-writing ☁ | Hook ≤ 1.5 s |
| 2 | narrative-engine-master 📁 | Story architecture, retention hooks, peak-end |
| 2 | ai-generated-content-humanization-director 📁 | Removes AI tells from script and plan |
| 2 | tamil-script-transcreation 📁 · tamil-voiceover-tts-director 📁 · tts-voiceover-director ☁ | Tamil version and VO rendering |
| 3 | storyboard-i2v 📁 · emotions 📁 · camera-angles-movements 📁 | Shot-list method and per-shot craft vocabulary |
| 3 | lighting-mood-color-tone ☁ · transition-types-between-shots ☁ · kinetic-typography-design ☁ | Per-shot craft, cloud only |
| 3 | product-ui-screen-integration-specialist 📁 · screen-content-compositing-specialist 📁 | When/how real product UI appears (T4) |
| 4 | stock-footage-search-curation-director ★📁 | Four-lane search, frame inspection, 6-axis scoring, contact sheet |
| 4 | semantic-broll-interleaving-director 📁 · rejection-log-taste-memory 📁 | Extra cutaways; rejection memory |
| 5 | production-control-sheet-generator 📁 | Talent Thief control sheet — bridge from board to render layer |
| 5 | text-on-screen-system 📁 · text-overlay-timing-director 📁 · tamil-text-overlay-typography 📁 | Overlay copy, timing, position, Tamil rendering (v2 implements their rules) |
| 5 | clipchamp-effects-transitions-director ☁ · copy-and-text-overlay ☁ · audio-strategy-video ☁ | Cloud-only counterparts |
| 5 | audio-department-master 📁 · bgm-placement-analyzer 📁 · sonic-brand-identity 📁 | Audio strategy, duck map, sting/audio logo rules |
| 5 | finishing-colorist 📁 | The unified grade v2 applies |
| 6 | post-production-qa-suite 📁 · raw-video-qa-analyzer 📁 · engagement-element-injector 📁 | G6 battery (qa_check.py automates the measurable half) |
| 6 | video-assembly-overlay-analyzer ☁ | Cloud only |
| 7 | caption-hashtag-cta-writer 📁 · comment-to-dm-funnel-designer 📁 · platform-versioning-publisher 📁 · content-calendar-architect 📁 · performance-prediction-ab-planner 📁 | Publish package |
| 7 | thumbnail-strategy ☁ · instagram-posting-time-format-optimizer ☁ | Cloud only |
| 8 | kpi-benchmark-diagnostic 📁 · content-repurposing-engine 📁 | Weekly learn loop |
| any | skills-gap-detector 📁 | Fail-safe when a skill is missing |

## 8. Known limits / next candidates

* Contact-sheet scoring is metadata-based (resolution, aspect, duration, lane, source). The **visual 6-axis scoring** in the curation skill still needs a vision pass (Claude looking at extracted frames) — the sheet is built to hold those scores.
* `pop` motion is a fast fade; FFmpeg `drawtext` cannot scale text over time. A true scale-pop needs an ASS subtitle pass (libass is available in this build) — worth adding if T3 anchors need more punch.
* Unsplash's download-tracking endpoint is not pinged (their API guideline for production apps).
* No LUT support yet; `unified_grade` is filter-based. A `.cube` LUT path would drop straight into `clip_normaliser.UNIFIED_GRADES`.
