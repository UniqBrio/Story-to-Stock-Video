# Story-to-Stock-Video (SVOS)

A scripted pipeline that turns a story plan spreadsheet into a finished, broadcast-spec
video — stock asset sourcing, clip normalisation, transitions, text overlays, audio
mixing, export and an automated QA pass.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) and `ffprobe` on your `PATH` — a current **full** build with
  libfreetype and HarfBuzz (on Windows, the gyan.dev "full" build). FFmpeg 6.1 misspells Tamil
  overlays by drawing vowel signs such as ை on the wrong side. Preflight checks this for you.
- `pip install -r requirements.txt` (openpyxl, Pillow)
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

## Self-test

```bash
python selftest.py
```

Generates synthetic media into `_selftest/` and runs the pipeline end to end — no API
keys needed. The offline fetcher and review-loop tests run with:

```bash
python -m unittest discover -s tests
```

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
