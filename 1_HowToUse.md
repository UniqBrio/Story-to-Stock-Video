# How to use Story-to-Stock-Video

This guide takes you from a fresh Windows machine to a finished, signed-off vertical video. It covers the one-time setup, the inputs you prepare, a complete worked example, and what to do each time the pipeline stops and asks you for something.

All commands are for **PowerShell**. Run them from the project folder, `C:\Business\Story-to-Stock-Video`.

---

## What you get

You fill in one Excel workbook and the pipeline produces:

| Output | Where |
|---|---|
| Final video (1080×1920, H.264, −14 LUFS, ready for Reels/Shorts) | `<output_folder>\<output_filename>` |
| Cover image (thumbnail) | `<output_folder>\<name>_cover.jpg` |
| QA report with a verdict: **READY FOR G6 REVIEW** or **FIX BEFORE G6** | `<output_folder>\qa_report.md` |
| Child-safety (U-rated) reports and sign-off form | `<output_folder>\safety\assets_report.html` and `final_report.html` |
| Downloaded stock clips and images | `<assets_folder>\Videos`, `<assets_folder>\Images` |

`output_folder` and `assets_folder` are set in the workbook. They default to `Output\` and `Assets\` inside the project folder.

---

## Part 1 — One-time setup

### 1.1 Install the tools

| Tool | Version | How to check |
|---|---|---|
| Python | 3.10 – 3.13 | `python --version` |
| FFmpeg **full** build (includes ffprobe, libfreetype, libass) | current release | `ffmpeg -version` |

For FFmpeg on Windows, download the "full" build from gyan.dev, unzip it, and add its `bin` folder to your `PATH`. The "essentials" build lacks libass, which draws the Tamil text; preflight tells you if it's missing.

### 1.2 Install the Python packages

```powershell
pip install -r requirements.txt
```

### 1.3 Download the content-safety models (once, about 1.2 GB)

```powershell
python content_safety.py --setup
```

This downloads CLIP and Whisper into `.models\` and verifies their checksums. Everything runs on your machine; nothing is uploaded. The pipeline will not render without these models.

### 1.4 Set your stock-footage API keys

You need at least one key. Pexels is the best source for vertical video. The keys are free:

- Pexels: https://www.pexels.com/api/
- Pixabay: https://pixabay.com/api/docs/
- Unsplash: https://unsplash.com/developers

Save them permanently (open a **new** PowerShell window afterwards):

```powershell
setx PEXELS_API_KEY   "your-pexels-key"
setx PIXABAY_API_KEY  "your-pixabay-key"
setx UNSPLASH_API_KEY "your-unsplash-key"
```

Keep keys out of the workbook. Environment variables override anything in the sheet.

### 1.5 Prove the install works

```powershell
python selftest.py
```

This builds a 7-shot test video from synthetic media, with no keys or downloads needed. It should end with `✅  SELFTEST PASSED`. The test video is in `_selftest\Output\`. If the self-test fails, fix that before going further; the error message names the missing piece.

Fonts need no setup. Inter and Noto Sans Tamil are bundled in `fonts\`.

---

## Part 2 — The inputs you prepare

### 2.1 The workbook: `story_plan.xlsx`

The workbook must have two sheets with these exact names: **Shot Plan** and **Project Settings**. The pipeline adds two more, **Download Log** and **Asset Tracker**, by itself.

If you have an older workbook, bring it up to date first. A backup is written automatically and none of your values are changed:

```powershell
python upgrade_story_plan.py story_plan.xlsx
```

#### Shot Plan sheet — one row per shot

Row 3 holds the column headers. Shots start on row 4. Columns are matched by header name, so their order doesn't matter.

| Column | Required? | What to enter |
|---|---|---|
| Shot ID | yes | `1`, `2`, `3`… (padded to `001` automatically) |
| Scene Description | yes | What the viewer sees, in plain words |
| Duration (s) | yes | Seconds on screen, e.g. `4` |
| Treatment | yes | `T1` text card · `T2` stock clip · `T3` stock clip + keyword overlay · `T4` your screen recording · `T5` logo/CTA card |
| Search Keywords | stock shots (T2/T3) | 2–3 search phrases separated by commas, best first, e.g. `stressed teacher desk night, tired person paperwork` |
| Asset Priority | stock shots | `video_first` (usual), `image_first`, `video_only`, `image_only` |
| Local File Path | T4 always; otherwise optional | Full path to your own file. If filled, no stock is fetched for that shot |
| Trim In (s) / Trim Out (s) | optional | Which part of the clip to use. Default is `0` to the duration |
| Color Grade | optional | `chaos` (problem), `pivot` (turning point), `cta` (close), `none` |
| Ken Burns | optional | Slow move on stills: `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `none` |
| Text Overlay | optional | The on-screen words. Keep keyword overlays to 4 words or fewer and cards to 8 or fewer |
| Text Start (s) / Text End (s) | if there is text | Seconds from the start **of this shot**. Must fit inside the shot |
| Text Position | optional | `center`, `lower_third`, `upper_third`, `bottom_center`, `lower_left`, `top_center` |
| Text Style | optional | `auto`, `keyword`, `caption`, `card`, `cta` |
| Transition Out | optional | `cut`, `dissolve`, `fade_black`, `fade_white`, `none` |
| Trans Dur (s) | with a transition | e.g. `0.5` |
| Status | optional | Leave as `pending`. Set to `swap` to get a different stock clip on the next run |
| Visual Style | optional | `illustration` forces a cartoon-only shot (see Part 5) |

#### Project Settings sheet — one setting per row

Column A holds the setting name and column B its value. Values start on row 3. These are the ones you need to set; everything else has a sensible default:

| Setting | Example | Notes |
|---|---|---|
| `project_name` | `Academy Admin Story` | |
| `output_folder` | `C:\VideoProjects\Output` | Where the final video goes |
| `assets_folder` | `C:\VideoProjects\Assets` | Where downloads go |
| `output_filename` | `academy_reel.mp4` | |
| `vo_path` | `C:\VideoProjects\VO\academy_vo.mp3` | Your recorded voiceover. Record it naturally, with pauses between sentences: the pipeline fits it to the shots (see Part 5). Leave blank for a music-only video |
| `logo_path` | `C:\VideoProjects\Brand\logo.png` | A transparent PNG works best. Keep your other logo versions (white, black, colour) in the **same folder**: if part of this logo would blend into the background, the most visible version is used automatically |
| `text_font` | `Inter` | The bundled brand font. If this says `Arial`, Arial is what you'll get |
| `music_path` / `bgm_path` | *(optional)* | Background music. Leave blank if you have none |
| `sting_path` | *(optional)* | Short end sound on the logo card |
| `logo_mode` | `end_only` | `end_only` puts the logo on the closing card only; `bug` adds a corner logo throughout |

Leave a cell **blank** rather than typing a placeholder path. A path that doesn't exist stops the run. For example, `C:\VO\voiceover.mp3` with no file there stops at preflight.

### 2.2 Media files

| File | Needed when |
|---|---|
| Voiceover (`.mp3` / `.wav`), about as long as the video | You want narration (normal case) |
| Logo PNG | You use a T5 logo card |
| Screen recordings (`.mp4`) | You use a T4 product shot |
| Music / BGM / sting | Optional |

---

## Part 3 — Worked example: a 20-second academy reel

### 3.1 Fill in the Shot Plan

Blank cells are left empty.

| Shot ID | Scene Description | Duration (s) | Treatment | Search Keywords | Asset Priority | Local File Path | Color Grade | Text Overlay | Text Start (s) | Text End (s) | Text Position | Text Style | Transition Out | Trans Dur (s) | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Academy owner buried in paperwork late at night | 4 | T3 | stressed teacher desk night, tired person paperwork | video_first | | chaos | 11:41 PM. Sunday. | 0.3 | 3.5 | center | keyword | cut | 0 | pending |
| 2 | Card: the cost | 3 | T1 | | | | none | ₹47,000 lost every year. | 0.2 | 2.8 | center | card | dissolve | 0.5 | pending |
| 3 | Owner calm and smiling at a phone dashboard | 4 | T2 | happy teacher relaxed, calm clean office desk | video_first | | pivot | | | | | | cut | 0 | pending |
| 4 | Attendance module, one tap | 4 | T4 | | | C:\VideoProjects\Demo\attendance.mp4 | none | | | | | | cut | 0 | pending |
| 5 | Logo card, UniqBrio CTA | 5 | T5 | | image_only | | cta | DM 'BRIO' to see it live | 0.5 | 4.6 | center | cta | none | 0 | pending |

The shots add up to 20 s. The 0.5 s dissolve overlaps shots 2 and 3, so the video is 19.5 s.

### 3.2 Fill in Project Settings

```
project_name     Academy Admin Story
output_folder    C:\VideoProjects\Output
assets_folder    C:\VideoProjects\Assets
output_filename  academy_reel.mp4
vo_path          C:\VideoProjects\VO\academy_vo.mp3
logo_path        C:\VideoProjects\Brand\logo.png
text_font        Inter
logo_mode        end_only
```

Save the workbook and **close it in Excel**. The pipeline writes logs into it and can't while Excel has it open.

### 3.3 Step 1 — Preflight (check before spending any API calls)

```powershell
python run_pipeline.py story_plan.xlsx --preflight
```

This reads the workbook and checks FFmpeg, fonts, keys, and every file you referenced. Expected ending:

```
  ✅  Preflight passed
```

Any `❌` line names exactly what to fix: a missing file, no API key, or no Tamil font. `⚠️` lines are warnings and won't stop the run.

### 3.4 Step 2 — Choose the footage (recommended)

Gather 8 candidates per stock shot without downloading anything:

```powershell
python run_pipeline.py story_plan.xlsx --contact-sheet 8
```

1. Open `contact_sheet.html` (next to `project.json` in the project folder).
2. Click the clip you want for each shot, or mark it **reject**.
3. Click **💾 Save picks.json**. It lands in your Downloads folder.

Then download the picks and render:

```powershell
python run_pipeline.py story_plan.xlsx --apply-picks "$env:USERPROFILE\Downloads\picks.json"
```

To skip choosing and let the pipeline pick the best match automatically, run this instead:

```powershell
python run_pipeline.py story_plan.xlsx
```

### 3.5 Step 3 — Child-safety review of the assets (if the run pauses)

Before rendering, every clip, image, text line and audio layer is checked for U-rated suitability. Each item gets one of three results:

- **All PASS**: the run continues by itself.
- **REVIEW**: the run stops with "Some items need your decision". Then:
  1. Open `<output_folder>\safety\assets_report.html`.
  2. Click **Approve** or **Reject** on each flagged item.
  3. Click **Save safety_review.json**.
  4. Continue:
     ```powershell
     python run_pipeline.py story_plan.xlsx --apply-safety-review "$env:USERPROFILE\Downloads\safety_review.json" --from 3
     ```
- **FAIL**: nothing is rendered. A stock clip that fails is rejected automatically, so fetch a replacement with:
  ```powershell
  python run_pipeline.py story_plan.xlsx --from 2
  ```
  If one of *your own* files fails, replace it and rerun.

### 3.6 Step 4 — Read the result

A successful run ends like this:

```
  ✅  ALL PHASES COMPLETE  (2m 10s)
  Output    : C:\VideoProjects\Output\academy_reel.mp4
  Cover     : C:\VideoProjects\Output\academy_reel_cover.jpg
  QA report : C:\VideoProjects\Output\qa_report.md   → verdict: READY FOR G6 REVIEW
  Safety    : PASS  ·  sign-off MISSING — do not publish
```

Open `qa_report.md`. If the verdict is **FIX BEFORE G6**, each ❌ line says what's wrong, for example a missing CTA, wrong font, missing shot or loudness off target. Fix the sheet or the file, then re-render without re-downloading:

```powershell
python run_pipeline.py story_plan.xlsx --from 3 --force
```

### 3.7 Step 5 — Watch it like a viewer (G6)

Copy the MP4 to your phone and watch it **with the sound off first**. Can someone follow the story from the text alone? Then watch with sound. Decide: **SHIP**, **FIX**, or **KILL**.

To swap a shot you don't like:

```powershell
python validation_report.py project.json
```

1. Open `validation_report.html`, click **Keep** or **Swap** per shot, and save `review.json`.
2. Then run:
   ```powershell
   python run_pipeline.py story_plan.xlsx --apply-review "$env:USERPROFILE\Downloads\review.json"
   ```

A swapped clip is never offered again for that shot.

### 3.8 Step 6 — Final U-rated sign-off (required before publishing)

1. Open `<output_folder>\safety\final_report.html`.
2. Tick the three checks, type your name, and click **Save**.
3. Record the sign-off and confirm it:
   ```powershell
   python content_safety.py project.json --apply-review "$env:USERPROFILE\Downloads\safety_review.json"
   python content_safety.py project.json --status
   ```

If Downloads already has a `safety_review.json` from step 3.5, the browser saves the new one as `safety_review (1).json`. Use the newest file.

The sign-off is tied to this exact video file by checksum. If you re-render, sign off again. Publish only when `--status` shows the sign-off recorded for the current render.

---

## Part 4 — When the pipeline stops

The pipeline stops on purpose instead of producing a broken video. The last lines always say why.

| Message | What to do |
|---|---|
| `Preflight FAILED` + `vo_path set but file missing` | Fix the path in Project Settings, or clear the cell |
| `no provider key is set` | Set at least one API key (step 1.4) and open a new PowerShell window |
| `Phase 2 finished with missing assets` | No stock found for a shot. Change its Search Keywords, or use the AI fallback (below) |
| `Refusing to continue — a dropped shot…` | A shot has no source file. Fix it, or use `--allow-drop` for a rough draft only |
| `Content-safety models are missing` | `python content_safety.py --setup` |
| `Could not update Excel logs` | Close `story_plan.xlsx` in Excel and rerun |
| QA: `overlay rendered in FFmpeg's built-in default font` | Check `text_font` and that the `fonts\` folder is present |

**When stock can't fill a shot (AI image fallback):**

1. Write image prompts for the empty shots:
   ```powershell
   python prompt_generator.py project.json
   ```
2. Open the generated prompts, create the images in your image tool, and save them as `shot_<ID>_*.jpg` in `<assets_folder>\Images`. For example, `shot_003_a.jpg`.
3. Link the images to their shots:
   ```powershell
   python prompt_generator.py project.json --link
   ```
4. Re-render:
   ```powershell
   python run_pipeline.py story_plan.xlsx --from 3
   ```

---

## Part 5 — Things to know

- **Assets are remembered between runs.** Re-running keeps each shot's clip while its keywords, scene and asset priority are unchanged. To force a new clip, set **Status** to `swap`. To start completely fresh, run `python story_reader.py story_plan.xlsx --fresh`.
- **`--force` re-renders; it never re-downloads.** `--refetch` re-downloads everything and discards your picks, so use it rarely.
- **Swimming, beach, pool and swimwear shots are cartoon-only.** These subjects are searched only in illustrations, and any real photo of them fails the safety check.
- **Tamil text works**, using the bundled Noto Sans Tamil. Tamil speech transcription is approximate, so the safety report asks you to listen to the VO yourself before signing off.
- **The voiceover is fitted to the shots for you.** Record naturally; you don't need to match the video's timing. After preflight, the pipeline finds each spoken phrase, works out which shot it belongs to (in order, using the **VO Line** column if filled, otherwise the on-screen text and scene description), shortens long pauses, and lengthens any shot too short for its words. Shots are never shortened. The result is printed as a timing map and saved to `<output_folder>\vo_timing.md`. If the pipeline can't tell confidently which words go with which shot, it stops **before** fetching or rendering and asks you. Either fill the VO Line column for the shots it names (it suggests the text) and rerun, or accept its plan with `--accept-vo`.
- **On-screen text is kept consistent with the voice.** If a stock shot's on-screen text shares no meaningful word with what's said over it, it is replaced by a short line taken from the spoken words. For example, "Normal Tuesday." under "By Tuesday, you are already exhausted." becomes "You are already exhausted." Numbers, times and day names ("11:41 PM. Sunday.") count as deliberate details and are kept. Cards and the logo/CTA card are never changed. It only happens when the transcript is reliable or you typed a VO Line, and every change is listed in `vo_timing.md`. To switch it off, set `match_text_to_vo` to `false` in Project Settings.
- **Every overlay stays up long enough to read:** 1.5 s for one word, 2 s for up to 4, 2.5 s for up to 7, and 3.5 s beyond that, plus 0.5 s for Tamil. A shot that's too short for its text is lengthened. Shots are never shortened.
- **The logo picks itself for visibility.** If any part of your logo would disappear against the logo card colour (for example a purple "U" on a purple card), the most visible transparent version from the same folder is used instead. Phase 1 tells you when this happens. For the corner logo (`logo_mode` = `bug`), the footage behind it is sampled. Logos with their own solid background are always used as you chose them.
- **Keep the workbook closed** in Excel while the pipeline runs.

---

## Command cheat sheet

```powershell
python selftest.py                                                  # check the install
python run_pipeline.py story_plan.xlsx --preflight                  # check inputs only
python run_pipeline.py story_plan.xlsx --contact-sheet 8            # choose footage
python run_pipeline.py story_plan.xlsx --apply-picks <picks.json>   # download picks + render
python run_pipeline.py story_plan.xlsx                              # auto-pick + render
python run_pipeline.py story_plan.xlsx --apply-safety-review <safety_review.json> --from 3
python run_pipeline.py story_plan.xlsx --from 3 --force             # re-render, keep assets
python run_pipeline.py story_plan.xlsx --from 2                     # fetch replacements
python validation_report.py project.json                            # keep / swap review
python run_pipeline.py story_plan.xlsx --apply-review <review.json>
python content_safety.py project.json --status                      # is this render signed off?
```
