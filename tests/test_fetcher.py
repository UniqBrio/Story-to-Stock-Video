#!/usr/bin/env python3
"""
Offline tests for stock sourcing and the review loop — no API keys, no network.

Provider responses are canned; downloads copy a synthetic clip/still made with
FFmpeg. Run:  python -m unittest discover -s tests -v
"""

import io
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
import contextlib
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import asset_fetcher as af          # noqa: E402
import story_reader as sr           # noqa: E402
import svos_common as sc            # noqa: E402

TW, TH = 1080, 1920


def pexels_video(vid, title, w=1080, h=1920, dur=10, author="Asha", files=None):
    return {"id": vid, "duration": dur, "image": f"https://img/{vid}.jpg",
            "url": f"https://www.pexels.com/video/{title.replace(' ', '-')}-{vid}/",
            "user": {"name": author},
            "video_files": files or [{"link": f"https://v/{vid}.mp4", "width": w, "height": h}]}


class FakeAPI:
    """Routes a request URL to a canned payload; counts calls."""
    def __init__(self, routes: dict):
        self.routes = routes              # substring of the query → list of pexels videos
        self.calls = []

    def __call__(self, url, headers=None, timeout=20):
        self.calls.append(url)
        if "api.pexels.com/videos" in url:
            q = url.split("query=")[1].split("&")[0].replace("%20", " ")
            return {"videos": self.routes.get(q, [])}, None, "150"
        return {"videos": [], "hits": [], "photos": [], "results": []}, None, None


def fake_download_factory(src: Path):
    def fake(url, dest, max_mb=300, timeout=60):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest
    return fake


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="svos_test_"))
        cls.clip = cls.tmp / "clip.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30",
                        "-t", "1", "-pix_fmt", "yuv420p", str(cls.clip)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.tmp))
        af.configure({}, self.work / "cache")
        # Content-safety screening is tested in test_safety.py; here every download passes unless a test says otherwise
        self.screen = mock.patch.object(af, "safety_screen", return_value=("PASS", []))
        self.ready = mock.patch("content_safety.readiness", return_value=[])
        self.screen.start(); self.ready.start()
        self.addCleanup(self.screen.stop); self.addCleanup(self.ready.stop)


class RenditionAndScoring(Base):
    def test_pick_file_prefers_smallest_full_res_same_orientation(self):
        files = [{"link": "4k", "width": 2160, "height": 3840}, {"link": "hd", "width": 1080, "height": 1920},
                 {"link": "sd", "width": 540, "height": 960}, {"link": "land", "width": 3840, "height": 2160}]
        self.assertEqual(af._pick_file(files, TW, TH)["link"], "hd")

    def test_pick_file_falls_back_to_largest_when_nothing_fills_frame(self):
        files = [{"link": "sd", "width": 540, "height": 960}, {"link": "md", "width": 720, "height": 1280}]
        self.assertEqual(af._pick_file(files, TW, TH)["link"], "md")

    def test_relevance_uses_provider_text(self):
        shot = {"keywords": ["stressed teacher desk"], "scene_desc": "Owner buried in paperwork at night"}
        good = {"text": "stressed teacher working at desk with paperwork"}
        bad = {"text": "sunset over the ocean"}
        self.assertGreater(af.relevance(good, shot), 0.5)
        self.assertEqual(af.relevance(bad, shot), 0.0)

    def test_matching_asset_outranks_unrelated_at_equal_quality(self):
        shot = {"shot_id": "001", "keywords": ["stressed teacher desk"], "scene_desc": "teacher at desk",
                "duration": 4, "asset_priority": "video_only"}
        api = FakeAPI({"stressed teacher desk": [pexels_video(1, "beach sunset waves"),
                                                  pexels_video(2, "stressed teacher at desk")]})
        with mock.patch.object(af, "_http_json", api):
            cands = af.gather_candidates(shot, {"pexels": "k"}, TW, TH, 5, set())
        self.assertEqual(cands[0]["id"], "2")

    def test_short_clips_and_excluded_assets_are_never_offered(self):
        shot = {"shot_id": "001", "keywords": ["q"], "duration": 6, "asset_priority": "video_only"}
        api = FakeAPI({"q": [pexels_video(1, "a", dur=3), pexels_video(2, "b"), pexels_video(3, "c")]})
        with mock.patch.object(af, "_http_json", api):
            cands = af.gather_candidates(shot, {"pexels": "k"}, TW, TH, 5, {"pexels:video:2"})
        self.assertEqual([c["id"] for c in cands], ["3"])


class Ladder(Base):
    def test_relaxes_queries_when_first_search_is_empty(self):
        shot = {"shot_id": "001", "keywords": ["stressed teacher desk paperwork night"], "scene_desc": "",
                "duration": 4, "asset_priority": "video_only"}
        api = FakeAPI({"stressed teacher desk": [pexels_video(9, "teacher at desk")]})
        with mock.patch.object(af, "_http_json", api), contextlib.redirect_stdout(io.StringIO()):
            cands = af.gather_candidates(shot, {"pexels": "k"}, TW, TH, 5, set())
        self.assertEqual([c["id"] for c in cands], ["9"])
        self.assertEqual(cands[0]["ladder"], 1)

    def test_drops_orientation_filter_as_last_rung(self):
        shot = {"shot_id": "001", "keywords": ["rare"], "scene_desc": "", "duration": 4, "asset_priority": "video_only"}
        def api(url, headers=None, timeout=20):
            vids = [pexels_video(5, "rare", w=1920, h=1080)] if "orientation" not in url else []
            return {"videos": vids}, None, None
        with mock.patch.object(af, "_http_json", api), contextlib.redirect_stdout(io.StringIO()):
            cands = af.gather_candidates(shot, {"pexels": "k"}, TW, TH, 5, set())
        self.assertEqual(cands[0]["ladder"], 2)


class RequestHygiene(Base):
    def test_budget_caps_requests(self):
        af.configure({"budget": {"pexels": 2}}, None)
        api = FakeAPI({})
        with mock.patch.object(af, "_http_json", api), contextlib.redirect_stdout(io.StringIO()):
            for i in range(5):
                af._api_get("pexels", f"https://api.pexels.com/videos/search?query=q{i}")
        self.assertEqual(len(api.calls), 2)

    def test_cache_serves_repeat_requests_and_hides_key(self):
        api = FakeAPI({})
        url = "https://pixabay.com/api/videos/?key=SECRET123&q=teacher"
        with mock.patch.object(af, "_http_json", api):
            af._api_get("pixabay", url, secret="SECRET123")
            af._api_get("pixabay", url, secret="SECRET123")
        self.assertEqual(len(api.calls), 1)
        for f in (self.work / "cache").rglob("*.json"):
            self.assertNotIn("SECRET123", f.read_text() + f.name)

    def test_rejected_key_stops_further_calls(self):
        calls = []
        def api(url, headers=None, timeout=20):
            calls.append(url)
            return None, "HTTP 403", None
        with mock.patch.object(af, "_http_json", api), contextlib.redirect_stdout(io.StringIO()):
            af._api_get("pexels", "https://api.pexels.com/v1/search?query=a")
            af._api_get("pexels", "https://api.pexels.com/v1/search?query=b")
        self.assertEqual(len(calls), 1)

    def test_long_retry_after_is_not_slept(self):
        err = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "3600"}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err), mock.patch("time.sleep") as slept:
            data, e, _ = af._http_json("https://api.pexels.com/v1/search?query=a")
        self.assertIsNone(data)
        self.assertIn("3600", e)
        slept.assert_not_called()


def make_project(work: Path, n: int = 2) -> Path:
    shots = [{"shot_id": f"00{i}", "scene_desc": f"teacher scene {i}", "duration": 4.0,
              "keywords": [f"teacher {i}"], "asset_priority": "video_only", "kind": "stock", "treatment": "T2",
              "status": "pending", "local_file": ""} for i in range(1, n + 1)]
    proj = {"project_name": "t", "width": TW, "height": TH, "assets_folder": str(work / "Assets"),
            "output_folder": str(work / "Output"), "api_keys": {"pexels": "k"}, "shots": shots}
    p = work / "project.json"
    p.write_text(json.dumps(proj), encoding="utf-8")
    return p


def run_main(*argv) -> int:
    with mock.patch.object(sys, "argv", ["asset_fetcher.py", *argv]), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            af.main()
        except SystemExit as e:
            return int(e.code or 0) if isinstance(e.code, int) else 1
    return 0


class ReviewLoop(Base):
    def routes(self):
        return {"teacher 1": [pexels_video(11, "teacher one"), pexels_video(12, "teacher one again", author="Ben")],
                "teacher 2": [pexels_video(21, "teacher two", author="Chen"), pexels_video(22, "teacher two b", author="Dee")]}

    def test_swap_refetches_a_different_asset_and_remembers_the_rejection(self):
        p = make_project(self.work)
        with mock.patch.object(af, "_http_json", FakeAPI(self.routes())), \
                mock.patch.object(af, "_download", fake_download_factory(self.clip)):
            self.assertEqual(run_main(str(p)), 0)
            first = json.loads(p.read_text())["shots"][0]
            self.assertEqual(first["asset_id"], "11")
            review = self.work / "review.json"
            review.write_text(json.dumps({"001": "swap", "002": "keep"}))
            self.assertEqual(run_main(str(p), "--apply-review", str(review)), 0)
        shots = json.loads(p.read_text())["shots"]
        self.assertEqual(shots[0]["asset_id"], "12")
        self.assertIn("pexels:video:11", shots[0]["rejected_ids"])
        self.assertTrue(shots[1].get("approved"))
        self.assertEqual(shots[1]["asset_id"], "21")

    def test_swap_with_no_alternative_exits_2(self):
        p = make_project(self.work, n=1)
        routes = {"teacher 1": [pexels_video(11, "teacher one")]}
        with mock.patch.object(af, "_http_json", FakeAPI(routes)), \
                mock.patch.object(af, "_download", fake_download_factory(self.clip)):
            self.assertEqual(run_main(str(p)), 0)
            review = self.work / "review.json"
            review.write_text(json.dumps({"001": "swap"}))
            self.assertEqual(run_main(str(p), "--apply-review", str(review)), 2)
        self.assertEqual(json.loads(p.read_text())["shots"][0]["status"], "error")

    def test_status_swap_in_project_triggers_refetch(self):
        p = make_project(self.work, n=1)
        with mock.patch.object(af, "_http_json", FakeAPI(self.routes())), \
                mock.patch.object(af, "_download", fake_download_factory(self.clip)):
            run_main(str(p))
            proj = json.loads(p.read_text())
            proj["shots"][0]["status"] = "swap"
            p.write_text(json.dumps(proj))
            self.assertEqual(run_main(str(p)), 0)
        self.assertEqual(json.loads(p.read_text())["shots"][0]["asset_id"], "12")

    def test_duplicate_picks_are_refused(self):
        p = make_project(self.work)
        cand = {"source": "pexels", "type": "video", "id": "77", "url": "https://v/77.mp4", "width": TW,
                "height": TH, "duration": 9, "thumb": "", "page_url": "", "author": "", "query": "q",
                "query_rank": 0, "pos": 0, "license": "", "score": 90}
        (self.work / "candidates.json").write_text(json.dumps({"001": [cand], "002": [cand]}))
        picks = self.work / "picks.json"
        picks.write_text(json.dumps({"001": 0, "002": 0}))
        with mock.patch.object(af, "_download", fake_download_factory(self.clip)):
            self.assertEqual(run_main(str(p), "--apply-picks", str(picks)), 2)
        shots = json.loads(p.read_text())["shots"]
        self.assertEqual(shots[0]["status"], "downloaded")
        self.assertEqual(shots[1]["status"], "swap")

    def test_bad_pick_index_does_not_crash(self):
        p = make_project(self.work, n=1)
        (self.work / "candidates.json").write_text(json.dumps({"001": []}))
        picks = self.work / "picks.json"
        picks.write_text("{not json")
        self.assertNotEqual(run_main(str(p), "--apply-picks", str(picks)), 0)


class SafetyInFetcher(Base):
    def test_unsafe_download_is_discarded_and_next_candidate_used(self):
        p = make_project(self.work, n=1)
        routes = {"teacher 1": [pexels_video(11, "teacher one"), pexels_video(12, "teacher two", author="Ben")]}
        verdicts = iter([("FAIL", ["swimwear 0.91"]), ("PASS", [])])
        with mock.patch.object(af, "_http_json", FakeAPI(routes)), \
                mock.patch.object(af, "_download", fake_download_factory(self.clip)), \
                mock.patch.object(af, "safety_screen", side_effect=lambda *a, **k: next(verdicts)):
            self.assertEqual(run_main(str(p)), 0)
        shot = json.loads(p.read_text())["shots"][0]
        self.assertEqual(shot["asset_id"], "12")
        self.assertIn("pexels:video:11", shot["rejected_ids"])
        self.assertEqual(shot["safety_rejections"][0]["reasons"], ["swimwear 0.91"])

    def test_review_download_is_not_auto_picked(self):
        p = make_project(self.work, n=1)
        routes = {"teacher 1": [pexels_video(11, "teacher one")]}
        with mock.patch.object(af, "_http_json", FakeAPI(routes)), \
                mock.patch.object(af, "_download", fake_download_factory(self.clip)), \
                mock.patch.object(af, "safety_screen", return_value=("REVIEW", ["possible alcohol 0.4"])):
            self.assertEqual(run_main(str(p)), 2)

    def test_cartoon_only_shot_searches_only_illustrations(self):
        shot = {"shot_id": "001", "keywords": ["kids swimming race"], "scene_desc": "", "duration": 4,
                "asset_priority": "video_first", "illustration_only": True}
        seen = []
        def api(url, headers=None, timeout=20):
            seen.append(url)
            return {"hits": [], "videos": [], "photos": [], "results": []}, None, None
        with mock.patch.object(af, "_http_json", api), contextlib.redirect_stdout(io.StringIO()):
            af.gather_candidates(shot, {"pexels": "k", "pixabay": "k", "unsplash": "k"}, TW, TH, 5, set())
        self.assertTrue(seen)
        self.assertFalse([u for u in seen if "pexels" in u or "unsplash" in u])
        self.assertTrue(all("video_type=animation" in u or "image_type=illustration" in u for u in seen))

    def test_missing_models_stop_the_fetch(self):
        p = make_project(self.work, n=1)
        with mock.patch("content_safety.readiness", return_value=["models missing"]):
            self.assertNotEqual(run_main(str(p)), 0)


class CarryForward(Base):
    def xlsx(self, keywords="teacher 1", status="pending", local=""):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Shot Plan"
        ws.append(["t"]); ws.append([])
        ws.append(["Shot ID", "Scene Description", "Duration (s)", "Search Keywords", "Asset Priority",
                   "Local File Path", "Status", "Treatment"])
        ws.append([1, "teacher scene 1", 4, keywords, "video_only", local, status, "T2"])
        ps = wb.create_sheet("Project Settings")
        ps.append(["Project Settings"]); ps.append(["Setting", "Value"])
        ps.append(["output_folder", str(self.work / "Output")])
        path = self.work / "plan.xlsx"
        wb.save(path)
        return path

    def prev(self):
        f = self.work / "shot_001.mp4"
        shutil.copy2(self.clip, f)
        prev = {"shots": [{"shot_id": "001", "scene_desc": "teacher scene 1", "keywords": ["teacher 1"],
                           "asset_priority": "video_only", "local_file": str(f), "status": "downloaded",
                           "source": "pexels", "asset_type": "video", "asset_id": "11", "approved": True,
                           "rejected_ids": ["pexels:video:5"]}]}
        p = self.work / "project.json"
        p.write_text(json.dumps(prev))
        return p, f

    def build(self, xlsx, prev):
        with contextlib.redirect_stdout(io.StringIO()):
            project, warnings, _ = sr.build_project(xlsx, prev)
        return project["shots"][0], warnings

    def test_keeps_fetched_asset_when_brief_unchanged(self):
        prev, f = self.prev()
        shot, _ = self.build(self.xlsx(), prev)
        self.assertEqual(shot["local_file"], str(f))
        self.assertTrue(shot["approved"])
        self.assertEqual(shot["rejected_ids"], ["pexels:video:5"])

    def test_changed_keywords_fetch_a_new_asset(self):
        prev, _ = self.prev()
        shot, warnings = self.build(self.xlsx(keywords="classroom"), prev)
        self.assertEqual(shot["local_file"], "")
        self.assertTrue(any("keywords/scene changed" in w for w in warnings))

    def test_sheet_swap_rejects_previous_asset(self):
        prev, _ = self.prev()
        shot, _ = self.build(self.xlsx(status="swap"), prev)
        self.assertEqual(shot["local_file"], "")
        self.assertIn("pexels:video:11", shot["rejected_ids"])


class TamilShaping(unittest.TestCase):
    def test_probe_returns_bool_and_false_without_font(self):
        self.assertFalse(sc.ffmpeg_text_shaping(font_path=str(HERE / "no_such_font.ttf")))
        self.assertIsInstance(sc.ffmpeg_text_shaping(), bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
