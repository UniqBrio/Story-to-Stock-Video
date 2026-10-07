"""
Call to action: contrast, wording checks, the early chip, and the A/B variant naming.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import svos_common as sc      # noqa: E402
import vo_aligner as va       # noqa: E402

PURPLE = "#6708C0"


class Contrast(unittest.TestCase):
    def test_button_colour_passes_with_white_text(self):
        self.assertGreaterEqual(sc.contrast_ratio("#FFFFFF", sc.CTA_BUTTON), 4.4)
        self.assertLess(sc.contrast_ratio("#FFFFFF", "#DE7D14"), 3.0)          # why brand orange was not used

    def test_unreadable_cta_colour_is_replaced(self):
        self.assertEqual(sc.readable_color(PURPLE, "#FFFFFF"), "#FFFFFF")       # already fine → kept
        self.assertNotEqual(sc.readable_color(PURPLE, "#6A0DAD"), "#6A0DAD")    # purple on purple → switched
        self.assertGreaterEqual(sc.contrast_ratio(sc.readable_color(PURPLE, "#6A0DAD"), PURPLE), 3.0)


class Wording(unittest.TestCase):
    def test_action_keyword_and_benefit(self):
        p = sc.cta_parts("DM 'BRIO'")
        self.assertEqual((p["action"], p["keyword"], p["benefit"]), ("DM", "BRIO", False))
        self.assertTrue(sc.cta_parts("DM 'BRIO' to see it live")["benefit"])
        self.assertTrue(sc.cta_parts("Comment BRIO for a free demo")["benefit"])
        self.assertFalse(sc.cta_parts("Follow us")["benefit"])


def shot(sid, kind, text="", dur=4.0, t0=0.5, t1=2.5, trans=0.0):
    return {"shot_id": sid, "treatment": kind, "text_overlay": text, "duration": dur, "text_start": t0,
            "text_end": t1, "trans_dur": trans, "transition_out": "dissolve" if trans else "cut"}


class EarlyChip(unittest.TestCase):
    def project(self, **text):
        return {"text": text, "shots": [shot("004", "T3", "You are already exhausted."),
                                        shot("005", "T3", "Running your academy could feel this simple?", 5, 0.5, 3.0, 0.5),
                                        shot("006", "T5", "DM 'BRIO'", 6, 0.5, 5.5)]}

    def test_chip_goes_on_the_shot_before_the_end_card(self):
        self.assertEqual(sc.early_cta(self.project()), {"shot_id": "005", "text": "DM 'BRIO' →"})

    def test_chip_can_be_switched_off(self):
        self.assertIsNone(sc.early_cta(self.project(cta_early=False)))

    def test_no_chip_before_a_product_insert(self):
        p = self.project()
        p["shots"][1]["treatment"] = "T4"
        self.assertIsNone(sc.early_cta(p))

    def test_chip_gets_its_own_slot_after_the_shots_text(self):
        p = self.project()
        changes = va.fit_durations(p["shots"], {}, sc.early_cta(p))
        chip = p["shots"][1]["cta_chip"]
        self.assertGreaterEqual(chip["start"], p["shots"][1]["text_end"] + 0.5)          # never on top of the caption
        self.assertGreaterEqual(chip["end"] - chip["start"], va.reading_need(chip["text"]))
        self.assertIn(("005", 5.0, p["shots"][1]["duration"], "CTA chip"), changes)


class Variant(unittest.TestCase):
    def test_variant_files_are_suffixed(self):
        self.assertEqual(sc.variant_suffix({}), "")
        self.assertEqual(sc.variant_suffix({"variant": "B"}), "_B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
