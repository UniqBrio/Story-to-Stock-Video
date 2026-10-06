# Story-to-Stock-Video (SVOS)

A scripted pipeline that turns a story plan spreadsheet into a finished, broadcast-spec
video — stock asset sourcing, clip normalisation, transitions, text overlays, audio
mixing, export and an automated QA pass.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) and `ffprobe` on your `PATH` — a **full** build with libfreetype
  and libass (on Windows, the gyan.dev "full" build). Tamil overlays are drawn with libass, because
  FFmpeg's `drawtext` never reorders Tamil vowel signs such as ை, in any version. Preflight checks this for you.
- `pip install -r requirements.txt` (openpyxl, Pillow, and the local content-safety packages)
- `python content_safety.py --setup` once. It downloads about 1.2 GB once, checksum-verifies it, and keeps about 1 GB of local models:
  CLIP ViT-B/32 (ONNX) and Whisper small (ONNX, run on the Microsoft-signed onnxruntime so Windows Smart App Control allows it). NudeNet and RapidOCR ship inside their pip packages. Python 3.10–3.13 are supported.
- Fonts: Inter and Noto Sans Tamil ship in `fonts/` (SIL OFL) and are used before any system font

## Setup

Stock-footage API keys are read from the environment (they override the workbook):

```bash
export PEXELS_API_KEY=...
export PIXABAY_API_KEY=...
export UNSPLASH_API_KEY=...
```

Copy `project.example.json` to `project.json` and adjust paths and brand settings for
your own machine. `project.json` is git-ignored — it holds your keys and local paths.

## Run

```bash
python run_pipeline.py story_plan.xlsx
```

Phases run in sequence:

| # | Stage | What it does |
|---|-------|--------------|
| 1 | `story_reader` | Excel → `project.json` (+ validation, restraint audit) |
| — | `preflight` | Keys · fonts · FFmpeg · referenced files — critical findings stop the run |
| 2 | `asset_fetcher` | Stock assets (skipped for cards / product inserts) |
| 3 | `clip_normaliser` | Exact-duration clips · cards · grades · Ken Burns |
| 4 | `transition_engine` | Single-pass assembly + authoritative timeline |
| 5 | `overlay_engine` | Text overlays (styles · motion · safe zones · Tamil) + logo |
| 6 | `audio_mixer` | VO duck map · music · BGM · sting · loudness normalise |
| 7 | `final_export` | H.264 High · BT.709 · cover frame · render manifest |
| 8 | `qa_check` | Automated QA battery → `Output/qa_report.md` |

Useful flags: `--from N`, `--only N`, `--force`, `--skip-fetch`, `--no-qa`,
`--preflight` (phase 1 + checks only), `--allow-drop` (see below), `--apply-review F` (see below).

The pipeline fails loudly. If any shot ends up without an asset, the fetcher exits non-zero and
nothing is rendered. Phases 3–4 refuse to assemble a video with a missing shot, because a dropped
shot shortens the picture and the VO runs past it. Pass `--allow-drop` only for a deliberate draft
render. QA then still marks the result **FIX BEFORE G6**.
Run `python run_pipeline.py --help` for the full list.

### Reviewing and swapping assets

Fetched assets are remembered. Rerunning phase 1 keeps each shot's asset while its keywords,
scene and asset priority are unchanged. Change them, or set Status = `swap`, to get a new one.
`--fresh` on `story_reader.py` forgets everything.

```bash
python run_pipeline.py story_plan.xlsx --contact-sheet 8        # G4: pick per shot, "Save picks.json"
python run_pipeline.py story_plan.xlsx --apply-picks picks.json

python validation_report.py project.json                        # Keep / Swap per shot, "Save review.json"
python run_pipeline.py story_plan.xlsx --apply-review review.json
```

A swapped asset is recorded per shot and never offered again. If stock can't fill a shot, run
`python prompt_generator.py project.json`, generate the images, save them as
`shot_<ID>_*.jpg` in `Assets/Images`, then run `python prompt_generator.py project.json --link`.

### Stock search settings (Project Settings sheet)

| Setting | Default | Meaning |
|---|---|---|
| `max_queries_per_shot` | 3 | Keyword phrases searched per shot |
| `fetch_budget_pexels` / `_pixabay` / `_unsplash` | 150 / 300 / 40 | Requests per run per provider |
| `fetch_cache_hours` | 24 | Reuse API responses from `Assets/_cache/api` |
| `max_download_mb` | 300 | Largest single download |

Other utilities: `bgm_prompt_generator.py` writes a BGM brief from the story arc.

## U-rated content safety (mandatory)

Every picture, video frame, text, voiceover and caption must be suitable for children before it is
rendered, and the finished video must pass again and carry a recorded human sign-off before it is published.
Everything runs on your machine; nothing is uploaded.

| What | How it is checked |
|---|---|
| Stock clips, AI images, your screen recordings, the logo | CLIP detects nudity, swimwear and revealing clothing, weapons, violence, alcohol, drugs, smoking and horror. NudeNet gives a second opinion on nudity. RapidOCR reads any text in the frame. |
| On-screen text, VO script lines, captions (`captions_path`) | `safety/blocklist.txt` in English, Tamil and Tanglish. Add your own terms in `safety/blocklist_extra.txt`. |
| Voiceover, music, BGM, sting, final mix | Whisper transcribes the audio and the transcript goes through the same text check |
| Finished video | Every shot is re-checked frame by frame, plus the full audio and all rendered text |

Each item gets **PASS**, **REVIEW** or **FAIL**:

- **PASS** is cleared.
- **REVIEW** needs your approval in the report.
- **FAIL** must be replaced. Stock clips that fail are rejected automatically and re-fetched on `--from 2`.
  Only `python content_safety.py project.json --override KEY --reason "..."` can clear a FAIL, and it is logged.

The render stages refuse any file or text that has not been cleared.

**Swimwear-type subjects are cartoon-only.** A shot about swimming, swimwear, bikinis, beaches, pools,
water polo or surfing is marked cartoon-only in phase 1. For such a shot:

- the fetcher searches only Pixabay illustrations and animations;
- AI prompts ask for a children's-cartoon style;
- any real photo or real face fails the check.

Change the subject list with the `illustration_only_subjects` setting, or force a shot with the
optional **Visual Style** column (`illustration`).

The workflow:

```bash
python run_pipeline.py story_plan.xlsx               # the asset gate runs before Phase 3, the final gate after Phase 7
#   open Output/safety/assets_report.html or final_report.html → Approve / Reject → "Save safety_review.json"
python run_pipeline.py story_plan.xlsx --apply-safety-review safety_review.json --from 3
#   final report: tick the three checks, enter your name → Save → apply it the same way
python content_safety.py project.json --status       # is the current render signed off?
```

QA marks the video **FIX BEFORE G6** while any safety item is open. It says **do not publish** until the
sign-off matches this exact render, checked by SHA-256.

Settings (Project Settings sheet): `vo_language` (auto / en / ta), `captions_path`,
`illustration_only_subjects`, `safety_models_dir`, `safety_frames_per_second`.

Known limits:

- **Tamil speech.** Whisper small's Tamil transcripts are approximate, so the report asks you to listen before signing off.
- **Text inside frames.** OCR reads English and numbers, not Tamil.
- **Celebrities.** No local model can recognise them. Cartoon-only shots instead reject every real face.
- **Calibration.** The thresholds were set on a small labelled set, documented in the audit. The human sign-off stays mandatory for this reason.

## Self-test

```bash
python selftest.py
```

Generates synthetic media into `_selftest/` and runs the pipeline end to end — no API
keys needed. The offline fetcher and review-loop tests run with:

```bash
python -m unittest discover -s tests
```

The self-test also exercises the safety gates and the sign-off rules. It needs the models from
`python content_safety.py --setup`.

For the self-test: Set `SVOS_LOGO_PATH` (or place `assets/logo.png`) to use your own logo;
otherwise a plain wordmark is generated.

## Documentation

| Document | Contents |
|---|---|
| [README-Video-Generation.md](README-Video-Generation.md) | How a video gets generated, treatment decision system |
| [Story-to-Stock-Video-Operating-System.md](Story-to-Stock-Video-Operating-System.md) | The full SVOS stage model |
| [Stage-Run-Cards.md](Stage-Run-Cards.md) | Per-stage run cards |
| [Skill-Resolution-Map.md](Skill-Resolution-Map.md) | Skill resolution map |
| [RENDER-LAYER-v2-NOTES.md](RENDER-LAYER-v2-NOTES.md) | Render layer v2 design notes |
| [AUDIT-2026-09-07.md](AUDIT-2026-09-07.md) | Audit of the current implementation |
| [Production-Bible-TEMPLATE.md](Production-Bible-TEMPLATE.md) | Production bible template |

## Note

`story_plan.xlsx` is git-ignored — the workbook's Settings sheet stores API keys.
