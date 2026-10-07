"""
Logo visibility: the pipeline must never put a logo on a background it blends into.
Synthetic logos (no brand files needed):
    two_tone.png   purple 'U' block + white block on transparent  (like UniqBrioLogo_white_transparent)
    all_white.png  white on transparent
    all_black.png  black on transparent
    badge.png      opaque — carries its own background
"""

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import svos_common as sc      # noqa: E402

PURPLE, OFFWHITE, CHARCOAL = "#6708C0", "#FAF7F2", "#1E1E22"


def _logo(path: Path, colours, opaque_bg=None):
    im = Image.new("RGBA", (300, 100), opaque_bg or (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, c in enumerate(colours):
        d.rectangle((20 + i * 140, 20, 140 + i * 140, 80), fill=c)
    im.save(path)
    return str(path)


class LogoChoice(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.two_tone = _logo(self.tmp / "two_tone.png", [(106, 13, 173, 255), (255, 255, 255, 255)])
        self.white = _logo(self.tmp / "all_white.png", [(255, 255, 255, 255)] * 2)
        self.black = _logo(self.tmp / "all_black.png", [(0, 0, 0, 255)] * 2)
        self.badge = _logo(self.tmp / "badge.png", [(255, 255, 255, 255)] * 2, opaque_bg=(0, 0, 0, 255))

    def test_part_of_logo_hidden_on_purple_is_measured(self):
        v = sc.logo_visibility(self.two_tone, [PURPLE])
        self.assertGreater(v["hidden"], 0.3)                      # the purple block vanishes
        self.assertEqual(sc.logo_visibility(self.white, [PURPLE])["hidden"], 0.0)

    def test_switches_to_the_variant_that_reads(self):
        self.assertEqual(sc.best_logo(self.two_tone, [PURPLE])[0], self.white)
        self.assertEqual(sc.best_logo(self.two_tone, [OFFWHITE])[0], self.black)
        self.assertEqual(sc.best_logo(self.two_tone, [CHARCOAL])[0], self.white)

    def test_keeps_a_logo_that_is_already_visible(self):
        self.assertEqual(sc.best_logo(self.white, [CHARCOAL]), (self.white, ""))

    def test_opaque_badges_are_kept_and_never_offered(self):
        self.assertEqual(sc.best_logo(self.badge, [PURPLE]), (self.badge, ""))
        self.assertNotIn(self.badge, sc.logo_variants(self.two_tone))

    def test_worst_background_decides_for_the_corner_bug(self):
        pick, _ = sc.best_logo(self.two_tone, [(20, 20, 20), (240, 240, 240)])   # dark and light footage
        v = sc.logo_visibility(pick, [(20, 20, 20), (240, 240, 240)])
        own = sc.logo_visibility(self.two_tone, [(20, 20, 20), (240, 240, 240)])
        self.assertLessEqual(v["hidden"], own["hidden"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
