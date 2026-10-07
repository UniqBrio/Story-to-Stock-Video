#!/usr/bin/env python3
"""
U-rated content-safety tests.

Text / policy / gate tests need no models. Image and speech tests use the local models
(python content_safety.py --setup) and are skipped when they are not installed.
Run:  python -m unittest discover -s tests -v
"""

import io
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
import contextlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(HERE))

import content_safety as cs      # noqa: E402

MODELS_READY = not cs.readiness()


class TextPolicy(unittest.TestCase):
    def st(self, text):
        return cs.check_text(text)[0]

    def test_sexual_and_profanity_fail(self):
        for t in ("Sexy summer deal", "hot girls near you", "what the f.u.c.k", "s3xy", "brothels at night",
                  "தேவடியா", "otha", "bikini body"):
            self.assertEqual(self.st(t), cs.FAIL, t)

    def test_borderline_needs_review(self):
        for t in ("Beer and a cigarette", "போதை வேண்டாம்", "kill the boredom", "damn good"):
            self.assertEqual(self.st(t), cs.REVIEW, t)

    def test_no_false_positives_on_everyday_words(self):
        for t in ("Skills, stable schedules — hello Madurai!", "11:41 PM. Sunday.", "₹47,000. Gone. Every single year.",
                  "உங்கள் மதுரை அகாடமி — வகுப்பு ரத்து. காமெடி!", "நம்பிக்கை", "DM 'BRIO' to see it live",
                  "Shoot the ball, class of 2026", "Essex and Sussex academies", "Attendance in one tap"):
            self.assertEqual(self.st(t), cs.PASS, t)

    def test_extra_blocklist_file_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "extra.txt"
            f.write_text("fail custom zorblax*\n", encoding="utf-8")
            self.assertEqual(cs.Blocklist([f]).check("the zorblaxes are here")[0], cs.FAIL)


class Subjects(unittest.TestCase):
    def test_swimwear_subjects_force_cartoons(self):
        for t in ("Kids swimming competition", "Swimmers warming up", "beach volleyball camp", "Water polo final",
                  "district swimming pool"):
            self.assertTrue(cs.subject_needs_illustration([t]), t)

    def test_everyday_phrases_do_not(self):
        for t in ("Carpool to class", "Talent pool of coaches", "Diving into data", "Dance class", "cricket nets"):
            self.assertFalse(cs.subject_needs_illustration([t]), t)


class DecisionsAndGates(unittest.TestCase):
    def results(self):
        return [{"key": "a", "status": cs.PASS, "label": "a"}, {"key": "b", "status": cs.REVIEW, "label": "b"},
                {"key": "c", "status": cs.FAIL, "label": "c"}]

    def test_resolve_needs_approval_and_blocks_fail(self):
        p = {}
        overall, fails, reviews = cs.resolve(p, self.results())
        self.assertEqual((overall, len(fails), len(reviews)), (cs.FAIL, 1, 1))
        p["safety_state"]["approvals"]["b"] = {}
        p["safety_state"]["overrides"]["c"] = {"reason": "false positive on our logo, checked by hand"}
        self.assertEqual(cs.resolve(p, self.results())[0], cs.PASS)

    def test_rejection_beats_pass(self):
        p = {"safety_state": {"rejections": {"a": {}}}}
        self.assertIn("a", [r["key"] for r in cs.resolve(p, self.results())[1]])

    def test_require_helpers_track_content_not_names(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "clip.jpg"
            f.write_bytes(b"one")
            p = {"safety_state": {"cleared": {cs.media_key(f): {}, cs.text_key("Hello"): {}}}}
            self.assertEqual(cs.require_media(p, f), "")
            self.assertEqual(cs.require_text(p, "Hello"), "")
            self.assertTrue(cs.require_text(p, "Hello!"))
            self.assertTrue(cs.require_media(p, f, illustration_only=True))      # different use → re-check
            f.write_bytes(b"two, a different file under the same name")
            self.assertTrue(cs.require_media(p, f))

    def test_override_needs_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            pj = Path(d) / "project.json"
            p = {"output_folder": d, "assets_folder": d}
            pj.write_text("{}")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cs.override(p, pj, "k", "ok"), 1)
                self.assertEqual(cs.override(p, pj, "k", "our own logo, reviewed by two people"), 0)
            self.assertIn("k", p["safety_state"]["cleared"])


@unittest.skipUnless(MODELS_READY, "safety models not installed — python content_safety.py --setup")
class Pictures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = cs.Engines(cs.models_dir())
        cls.tmp = Path(tempfile.mkdtemp(prefix="svos_safety_test_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def judge(self, name, illu=False):
        return cs.judge_media(self.eng, FIX / name, Path(tempfile.mkdtemp(dir=self.tmp)), illustration_only=illu)

    def test_cartoon_swimwear_only_allowed_in_cartoon_only_shots(self):
        self.assertEqual(self.judge("cartoon_one_piece_swimsuit.png")["status"], cs.FAIL)
        self.assertEqual(self.judge("cartoon_one_piece_swimsuit.png", illu=True)["status"], cs.PASS)
        self.assertEqual(self.judge("cartoon_swimmer.png", illu=True)["status"], cs.PASS)

    def test_real_photo_fails_in_cartoon_only_shot(self):
        r = self.judge("real_photo_portrait_1906.jpg", illu=True)
        self.assertEqual(r["status"], cs.FAIL)
        self.assertTrue(any("real photo" in x or "real face" in x for x in r["reasons"]))

    def test_ordinary_content_passes(self):
        self.assertNotEqual(self.judge("real_photo_portrait_1906.jpg")["status"], cs.FAIL)
        self.assertEqual(self.judge("cartoon_teacher.png")["status"], cs.PASS)

    def test_visible_text_in_a_frame_is_read(self):
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (720, 400), (250, 250, 250))
        font = ImageFont.truetype(str(HERE / "fonts" / "Inter-Bold.ttf"), 72)
        ImageDraw.Draw(im).text((40, 150), "SEXY DEALS", font=font, fill=(0, 0, 0))
        f = self.tmp / "sign.png"
        im.save(f)
        r = cs.judge_media(self.eng, f, Path(tempfile.mkdtemp(dir=self.tmp)))
        self.assertEqual(r["status"], cs.FAIL)
        self.assertTrue(any("visible text" in x for x in r["reasons"]))


def _speak(text: str, wav: Path) -> None:
    """Synthesize test speech: espeak-ng where installed, otherwise the built-in Windows voice (SAPI)."""
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "en", "-w", str(wav), text], check=True)
    else:
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{text}'); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


class Flicker(unittest.TestCase):
    """A REVIEW-level object hit in one video frame is a misreading; two frames, or a FAIL, still count."""

    @staticmethod
    def _frames(*reasons):
        return [{"status": cs.REVIEW if r else cs.PASS, "reasons": list(r)} for r in reasons]

    def test_one_frame_weapon_review_is_dropped(self):
        rs = self._frames([], [], ["possible weapons 0.43"], [], [])
        notes = cs._drop_flicker(rs)
        self.assertTrue(all(r["status"] == cs.PASS for r in rs))
        self.assertIn("1 of 5 frames", notes[0])

    def test_two_frames_still_review(self):
        rs = self._frames(["possible weapons 0.41"], [], ["possible weapons 0.43"], [], [])
        cs._drop_flicker(rs)
        self.assertEqual(sum(r["status"] == cs.REVIEW for r in rs), 2)

    def test_nudity_and_fail_hits_are_never_dropped(self):
        rs = self._frames([], ["possible nudity 0.33"], [], [], [])
        rs[3] = {"status": cs.FAIL, "reasons": ["weapons 0.65"]}
        cs._drop_flicker(rs)
        self.assertEqual(rs[1]["status"], cs.REVIEW)
        self.assertEqual(rs[3]["status"], cs.FAIL)

    def test_stills_are_not_affected(self):
        rs = self._frames(["possible weapons 0.43"])
        cs._drop_flicker(rs)
        self.assertEqual(rs[0]["status"], cs.REVIEW)


class WhisperGuards(unittest.TestCase):
    """Music and tones must not turn into hallucinated text (it can trip the blocklist by chance)."""

    def test_repetition_loop_is_cut_to_one_copy(self):
        sys.path.insert(0, str(HERE / "safety"))
        from whisper_onnx import WhisperOnnx
        self.assertEqual(WhisperOnnx._loop_start([5, 7, 8, 7, 8, 7, 8]), 3)       # keep [5, 7, 8]
        self.assertEqual(WhisperOnnx._loop_start([9, 9, 9]), 1)
        self.assertEqual(WhisperOnnx._loop_start([1, 2, 3, 4, 5]), -1)

    @unittest.skipUnless(MODELS_READY, "needs the safety models")
    def test_music_like_tone_gives_no_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "tone.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=330:sample_rate=16000:duration=12", "-af",
                            "volume='if(lt(mod(t\\,1)\\,0.5)\\,0.5\\,0.2)':eval=frame", str(wav)], check=True)
            tr = cs.transcribe(cs.Engines(cs.models_dir()), wav, [""])
            self.assertEqual(tr["auto"], "")


@unittest.skipUnless(MODELS_READY and (shutil.which("espeak-ng") or sys.platform == "win32"),
                     "needs the safety models and espeak-ng (or Windows)")
class Speech(unittest.TestCase):
    def test_spoken_words_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "vo.wav"
            _speak("Join now for the sexy summer party", wav)
            eng = cs.Engines(cs.models_dir())
            tr = cs.transcribe(eng, wav, [""])
            self.assertIn("sexy", tr["auto"].lower())
            self.assertEqual(cs.check_text(tr["auto"])[0], cs.FAIL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
