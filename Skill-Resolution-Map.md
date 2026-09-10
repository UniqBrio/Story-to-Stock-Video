# SVOS v1 — Skill Resolution Map
Resolution order: cloud → local library (`C:\Explorations\Pull-skill-research-MultiLLMs\output`) → STOP + skills-gap-detector.
Verified against Claude Cloud session + `Claude_Skills_List_Updated.xlsx` on 2026-07-30.

## ★ Created for this flow (local library)
| Skill | Path | Stage |
|---|---|---|
| voice-input-intake-director | `output\voice-input-intake-director.md` | 0, 2 |
| stock-footage-search-curation-director | `output\stock-footage-search-curation-director.md` | 4 |

## ✅ Claude Cloud (use directly)
| Skill | Stage(s) |
|---|---|
| academy-owner-psychology-expert · content-psychology-agent · jobs-to-be-done-expert · parent-perspective-content-writer | 1 |
| pain-point-reel-script-engine · feature-demo-script-writer · before-after-content-builder · academy-math-content-writer · testimonial-content-builder · objection-handling-content-writer · founder-origin-story-series-writer · mission-vision-content-writer | 2 (one per video) |
| video-hook-writing · story-writing-30sec · tamil-script-transcreation | 2 |
| tts-voiceover-director · tamil-voiceover-tts-director | 2 (VO render) |
| storyboard-i2v · emotions · lighting-mood-color-tone · camera-angles-movements · transition-types-between-shots · kinetic-typography-design | 3 |
| production-control-sheet-generator · clipchamp-effects-transitions-director · text-overlay-timing-director · tamil-text-overlay-typography · copy-and-text-overlay · audio-strategy-video · bgm-placement-analyzer | 5 |
| raw-video-qa-analyzer · video-assembly-overlay-analyzer · engagement-element-injector | 6 |
| thumbnail-strategy · caption-hashtag-cta-writer · comment-to-dm-funnel-designer · instagram-posting-time-format-optimizer · content-calendar-architect | 7 |
| kpi-benchmark-diagnostic · content-repurposing-engine | 8 |
| skill-creator-v2 (meta) | — |

## 📁 Local library only (load the .md explicitly)
| Skill | Path (under `output\`) | Stage(s) |
|---|---|---|
| production-bible-manager | `production-bible-manager.md` | 0 |
| narrative-engine-master | `narrative-engine-master.md` | 2 |
| ai-generated-content-humanization-director | `ai-generated-content-humanization-director.md` (repo; not in cloud) | 2 |
| product-ui-screen-integration-specialist | `product-ui-screen-integration-specialist.md` | 3 |
| semantic-broll-interleaving-director | `semantic-broll-interleaving-director.md` | 4 |
| audio-department-master (optional single audio orchestrator) | `audio-department-master.md` | 5 |
| sonic-brand-identity | `sonic-brand-identity.md` | 5 |
| finishing-colorist | `finishing-colorist.md` | 5 |
| text-on-screen-system (optional merged overlay authority) | `text-on-screen-system.md` | 5 |
| post-production-qa-suite (optional merged QA authority) | `post-production-qa-suite.md` | 6 |
| platform-versioning-publisher | `platform-versioning-publisher.md` | 7 |
| performance-prediction-ab-planner | `performance-prediction-ab-planner.md` | 7 |
| rejection-log-taste-memory | `rejection-log-taste-memory.md` | 4, 8 |
| skills-gap-detector | `skills-gap-detector.md` | any (fail-safe) |

Note on merged skills: where both granular cloud skills and a merged local authority exist (audio, overlays, QA), the merged local skill WINS when loaded; the cloud granular skills are the fallback when running cloud-only.

## 🔎 Local-library scan (2026-09-05)
Present as `.md` (or `<name>/SKILL.md`) in `output\`: all ★ and 📁 skills above **plus** these cloud-listed ones now also exist locally: academy-owner-psychology-expert · content-psychology-agent · jobs-to-be-done-expert · parent-perspective-content-writer · pain-point-reel-script-engine · academy-math-content-writer · testimonial-content-builder · founder-origin-story-series-writer · mission-vision-content-writer · tamil-script-transcreation · tamil-voiceover-tts-director · storyboard-i2v · emotions · camera-angles-movements · screen-content-compositing-specialist · production-control-sheet-generator · text-overlay-timing-director · tamil-text-overlay-typography · bgm-placement-analyzer · raw-video-qa-analyzer · engagement-element-injector · caption-hashtag-cta-writer · comment-to-dm-funnel-designer · content-calendar-architect · kpi-benchmark-diagnostic · content-repurposing-engine.

**Cloud-only, NOT in the local library (14)** — unavailable to a headless/local run unless the cloud skill is installed: feature-demo-script-writer · before-after-content-builder · objection-handling-content-writer · video-hook-writing · tts-voiceover-director · lighting-mood-color-tone · transition-types-between-shots · kinetic-typography-design · clipchamp-effects-transitions-director · copy-and-text-overlay · audio-strategy-video · video-assembly-overlay-analyzer · thumbnail-strategy · instagram-posting-time-format-optimizer. Local merged authorities cover most of them (text-on-screen-system ⊃ copy-and-text-overlay + kinetic-typography-design; audio-department-master ⊃ audio-strategy-video; post-production-qa-suite ⊃ video-assembly-overlay-analyzer; narrative-engine-master ⊃ video-hook-writing).

## ❌ Known absent (by design, logged)
- stock-visual-continuity-grader — absorbed into stock-footage-search-curation-director (continuity axis) + finishing-colorist (grade matching). Create as a standalone skill only if continuity failures recur.
