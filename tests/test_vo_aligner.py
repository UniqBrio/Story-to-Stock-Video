"""
Voiceover → shot alignment decisions (no audio needed: phrases and transcripts are given).
Mirrors the real 'My Academy Story' case, including Whisper's slips ('Cheese to cheese' for 'Fees to chase').
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import vo_aligner as va      # noqa: E402


def shot(sid, dur, text, scene, kind="T3", trans=0.0):
    return {"shot_id": sid, "duration": dur, "text_overlay": text, "scene_desc": scene, "treatment": kind,
            "trans_dur": trans, "transition_out": "dissolve" if trans else "cut"}


SHOTS = [
    shot("001", 4, "11:41 PM. Sunday.", "Academy owner buried in admin paperwork at desk late night"),
    shot("002", 1, "Fees.", "Quick montage — owner on phone chasing fees"),
    shot("003", 1, "Schedules.", "Quick montage — WhatsApp messages flooding screen"),
    shot("004", 3, "Normal Tuesday.", "Owner looks exhausted, resigned expression", trans=0.5),
    shot("005", 5, "Then. Something changed.", "Clean calm desk — owner smiling at phone dashboard", trans=0.5),
    shot("006", 6, "DM 'BRIO'", "Logo card — UniqBrio CTA", kind="T5"),
]
SEGS = [(1.17, 1.85), (2.40, 3.37), (4.11, 5.79), (6.98, 7.66), (8.18, 9.22), (10.03, 10.82), (11.35, 12.32),
        (13.62, 14.10), (14.88, 16.86), (18.17, 19.17), (19.97, 21.39), (22.63, 24.23), (24.97, 25.78)]
HEARD = ["late nights.", "Endless paperwork.", "and an academy that never stops.", "Cheese to cheese.",
         "Schedules to manage", "by Tuesday.", "you are already exhausted.", "But what if...",
         "Running your Academy could feel this simple.", "meat unique braille.", "",
         "speech, trying, and spirit.", "We handle the rest."]
EXPECTED = [(0, 3), (3, 4), (4, 5), (5, 7), (7, 9), (9, 13)]


class Matching(unittest.TestCase):
    def test_fuzzy_words_survive_transcription_slips(self):
        self.assertGreaterEqual(va.similarity("owner on phone chasing fees", "fees to chase"), 0.6)
        self.assertGreaterEqual(va.similarity("Logo card UniqBrio uniqbrio uniq brio", "meet unique brio"), 0.3)
        self.assertEqual(va.similarity("Fees.", ""), 0.0)

    def test_vo_line_must_be_heard_nearly_word_for_word(self):
        self.assertEqual(va.similarity("by Tuesday you are already exhausted", "by tuesday you are already exhausted",
                                       exact=True), 1.0)
        self.assertLess(va.similarity("by Tuesday you are already exhausted", "by tuesday", exact=True), 0.5)

    def test_brand_words_come_from_the_logo_folder(self):
        p = {"logo": {"path": r"C:\x\Inputs\Logos\UniqBrio\logo.png"}}
        self.assertEqual(va.brand_words(p), "uniqbrio uniq brio")


class Alignment(unittest.TestCase):
    def test_real_case_puts_every_phrase_on_its_shot(self):
        brand = "uniqbrio uniq brio"
        self.assertEqual(va.align([dict(s) for s in SHOTS], SEGS, HEARD, brand), EXPECTED)

    def test_order_is_kept_and_every_phrase_is_used(self):
        ranges = va.align([dict(s) for s in SHOTS], SEGS, HEARD, "")
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(SEGS))
        for (a, b), (c, d) in zip(ranges, ranges[1:]):
            self.assertEqual(b, c)

    def test_pauses_are_shortened_when_fitting(self):
        # phrases 0–2: 3.33 s of speech; their two pauses (0.55 s, 0.74 s) are capped at 0.35 s each → 4.03 s
        self.assertAlmostEqual(va.speech_span(SEGS, 0, 3), 4.03, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
