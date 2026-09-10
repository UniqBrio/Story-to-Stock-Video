# Story-to-Stock-Video (SVOS)

A scripted pipeline that turns a story plan spreadsheet into a finished, broadcast-spec
video — stock asset sourcing, clip normalisation, transitions, text overlays, audio
mixing, export and an automated QA pass.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) and `ffprobe` on your `PATH`
- `pip install -r requirements.txt`

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
| 2 | `asset_fetcher` | Stock assets (skipped for cards / product inserts) |
| 3 | `clip_normaliser` | Exact-duration clips · cards · grades · Ken Burns |
| 4 | `transition_engine` | Single-pass assembly + authoritative timeline |
| 5 | `overlay_engine` | Text overlays (styles · motion · safe zones · Tamil) + logo |
| 6 | `audio_mixer` | VO duck map · music · BGM · sting · loudness normalise |
| 7 | `final_export` | H.264 High · BT.709 · cover frame · render manifest |
| 8 | `qa_check` | Automated QA battery → `Output/qa_report.md` |

Useful flags: `--from N`, `--only N`, `--force`, `--skip-fetch`, `--no-qa`.
Run `python run_pipeline.py --help` for the full list.

Optional utilities: `validation_report.py` (review fetched assets side by side),
`prompt_generator.py` (AI image prompts for shots with no usable stock),
`bgm_prompt_generator.py` (BGM brief from the story arc).

## Self-test

```bash
python selftest.py
```

Generates synthetic media into `_selftest/` and runs the pipeline end to end — no API
keys needed. Set `SVOS_LOGO_PATH` (or place `assets/logo.png`) to use your own logo;
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
