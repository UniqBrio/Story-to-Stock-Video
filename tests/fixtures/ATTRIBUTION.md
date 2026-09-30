# Test fixtures

| File | Source | Licence |
|---|---|---|
| `real_photo_portrait_1906.jpg` | Portrait photograph of Franz Kafka (c. 1906), downscaled; copy shipped with lakeraai/onnx_clip | Public domain |
| `cartoon_one_piece_swimsuit.png` | OpenMoji emoji U+1FA71 "one-piece swimsuit", downscaled on white | CC BY-SA 4.0 — OpenMoji (https://openmoji.org) |
| `cartoon_swimmer.png` | OpenMoji emoji U+1F3CA "person swimming", downscaled on white | CC BY-SA 4.0 — OpenMoji (https://openmoji.org) |
| `cartoon_teacher.png` | OpenMoji emoji U+1F469 U+200D U+1F3EB "woman teacher", downscaled on white | CC BY-SA 4.0 — OpenMoji (https://openmoji.org) |

Used only by `tests/test_safety.py` to check the U-rated gate: a real photo must fail in a
cartoon-only (swimwear-subject) shot, and cartoon swimwear must fail everywhere else.
