#!/usr/bin/env python3
"""
content_safety.py  (SVOS — U-rated content gate)
─────────────────
Every picture, frame, word and sound that can reach the published video is
checked by LOCAL models (nothing is uploaded) and must be cleared before it is
rendered, and the final video must pass again and carry a recorded human
sign-off before it is published.

  Pictures / video frames
    • CLIP ViT-B/32 (ONNX) zero-shot: nudity · swimwear / lingerie / revealing
      clothing · weapons · violence · alcohol · drugs / smoking · horror, and
      real-photo vs cartoon style
    • NudeNet: exposed / covered intimate areas (second opinion)
    • RapidOCR: text visible inside footage, screen recordings and logos
  Text — on-screen overlays, VO script lines, captions, OCR text, transcripts
    • safety/blocklist.txt (+ blocklist_extra.txt): English, Tamil, Tanglish
  Sound — voiceover, music, BGM, sting and the final mix
    • Whisper small (ONNX, run on onnxruntime — safety/whisper_onnx.py) transcript → the text check

  Illustration-only subjects: a shot whose subject involves swimwear (swimming,
  bikini, beach, pool …) may only use cartoon / illustrated / animated visuals —
  a real photo or real face in such a shot FAILS.

Verdicts per item: PASS · REVIEW (needs a recorded human approval) · FAIL (must
be replaced; only an explicit --override with a written reason clears it).

Usage:
    python content_safety.py --setup                              # download + verify the models once
    python content_safety.py project.json --stage assets          # before rendering (run_pipeline does this)
    python content_safety.py project.json --stage final           # the exported video (run_pipeline does this)
    python content_safety.py project.json --apply-review safety_review.json
    python content_safety.py project.json --override KEY --reason "why this is safe"
    python content_safety.py project.json --status
    python content_safety.py --check-text "any text"

Exit codes: 0 cleared · 1 FAIL · 3 needs human review · 4 models / packages missing.
"""

from __future__ import annotations

import os
import re
import sys
import json
import html
import time
import base64
import hashlib
import tarfile
import argparse
import tempfile
import subprocess
import unicodedata
import urllib.request
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
POLICY_VERSION = "u-rated-2026-10-07.2"     # bump on any prompt/threshold change: re-checks every item

# ── Models (downloaded once by --setup, verified by SHA-256) ─────────────────
_CLIP_BASE = ("https://clip-as-service.s3.us-east-2.amazonaws.com/"
              "models-436c69702d61732d53657276696365/onnx/ViT-B-32")
MODELS = {
    "clip": {
        "dir": "clip-vit-b32",
        "files": {
            "visual.onnx":  (f"{_CLIP_BASE}/visual.onnx",
                             "06395063c0a5c28b1a8d4bd585261501a878c8f52d1216db6c4cbb651f7c13f1"),
            "textual.onnx": (f"{_CLIP_BASE}/textual.onnx",
                             "0af04c287a3be2570eaef7a1ef896d81c1989602df67a8905941afed589e545e"),
        },
    },
    "whisper": {
        "dir": "sherpa-onnx-whisper-small",
        "archive": ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-small.tar.bz2",
                    "486a46afbb7ba798507190ffe02fea2dd726049af212e774537efac6afb210a6"),
        "keep": ["small-encoder.int8.onnx", "small-decoder.int8.onnx", "small-tokens.txt"],
    },
}
# Whisper runs on onnxruntime directly (safety/whisper_onnx.py), not the sherpa-onnx package: its unsigned
# DLLs are blocked by Windows Smart App Control, while onnxruntime's wheel is Microsoft-signed.
PACKAGES = {"numpy": "numpy", "PIL": "pillow", "onnxruntime": "onnxruntime", "regex": "regex",
            "nudenet": "nudenet"}
# OCR: rapidocr 3.x (any Python ≥ 3.8, models bundled in the wheel); rapidocr_onnxruntime (≤ 3.12) still works
OCR_MODULES = ("rapidocr", "rapidocr_onnxruntime")


def _sfx(project: dict | None) -> str:
    """Report-file suffix for the A/B variant render ('' for the main render)."""
    v = str((project or {}).get("variant") or "").strip()
    return f"_{v}" if v else ""


def models_dir(project: dict | None = None) -> Path:
    d = (project or {}).get("safety", {}).get("models_dir") or os.environ.get("SVOS_MODELS_DIR") or (HERE / ".models")
    return Path(d)


# ── Policy: prompts and thresholds (calibrated 2026-09-30, see AUDIT) ────────
UNSAFE_PROMPTS = {
    "nudity":   ["a photo of a naked person", "nudity", "an explicit sexual image", "a sexually suggestive photo",
                 "a person in a sexually suggestive pose"],
    "swimwear": ["a person wearing a bikini", "a woman in a swimsuit", "a man in swimming trunks", "a bikini swimsuit",
                 "a person in lingerie or underwear", "a bra or underwear", "a person wearing revealing clothing",
                 "a woman in a low-cut top showing cleavage", "a person wearing a very short miniskirt",
                 "a shirtless person at a swimming pool"],
    "weapons":  ["a gun", "a rifle", "a pistol", "a knife or blade used as a weapon", "an axe", "a sword"],
    "violence": ["a violent scene", "blood and gore", "an execution or torture device", "a person being hurt",
                 "people fighting"],
    "alcohol":  ["a bottle of beer", "a glass of wine", "whiskey or liquor", "people drinking alcohol"],
    "drugs":    ["a person smoking a cigarette", "drugs or a syringe", "pills and drug abuse"],
    "horror":   ["a scary horror image", "a creepy frightening monster"],
}
SAFE_PROMPTS = [
    "a photo of people in a classroom", "a teacher with students", "a school bus", "a library", "an office desk",
    "a laptop computer", "a smartphone", "a website or app screenshot", "a user interface on a computer screen",
    "a comic book or cartoon", "a musical instrument", "a guitar", "a person playing music", "a sports ball",
    "children playing sports in sports uniforms", "an athlete in a fully clothed sports kit", "a sports jersey or t-shirt",
    "gym equipment", "a dance class", "a stage performance", "a graduation gown", "a person wearing a sweater",
    "a person wearing a jacket", "traditional Indian clothing", "a person wearing a kimono or robe", "food on a plate",
    "a burger or sandwich", "fruit", "a dog", "a pet cat", "a landscape", "a beach or lake with no people", "a building",
    "a fence", "a clock", "text on a plain background", "a logo on a plain background", "a colorful abstract pattern",
    "television test pattern color bars", "a blurred background", "a gymnast on a balance beam",
    "a person lifting weights in a gym", "a stuffed toy", "a quilt or blanket", "a scoreboard", "a menu", "an envelope",
    "a helmet", "a lawn game with balls and mallets", "a kitchen utensil", "a plain colored background",
    "a gradient background", "a chart or infographic", "a close-up portrait of a person's face",
    "a person wearing a suit and tie", "a person wearing a shirt", "an old black and white photo",
    "a cartoon of a person doing sports", "a surfer riding a wave", "a person running", "a family at home",
    "a title card with text on a solid colour background", "a call to action slide with a brand name",
    "a slide with a logo and text", "white words on a purple background",
    # everyday postures and close-ups: without these, CLIP's nearest match for a person slumped over books or
    # hands around a phone was "a person in a sexually suggestive pose" / "a photo of a naked person" (2026-10-07)
    "a tired person resting their head on a desk", "a person asleep on a stack of books", "a person sitting on the floor",
    "an exhausted person slumped over", "a fully clothed person lying down to rest",
    "a person sitting with their head in their hands", "a student studying with books", "a person napping",
    "a stressed person at work", "close-up of hands holding a smartphone", "a person's hands typing on a phone",
    "hands on a laptop keyboard", "a blurred beige wall", "a close-up of a hand",
    # desk objects: a phone edge over a spiral notebook read as "a knife or blade" (2026-10-07)
    "a spiral notebook on a desk", "a planner or notebook", "a phone lying on a notebook", "a vase with flowers",
    "a pen on a desk", "stationery on a desk", "a coffee mug", "a desk lamp",
]
PHOTO_PROMPTS = ["a photograph of a real person", "a real photo", "a photo of real people", "a video still of a real person",
                 "a product photo"]
ILLU_PROMPTS = ["a cartoon drawing", "a flat vector illustration", "an emoji", "an animated cartoon character",
                "a 3D cartoon render", "a hand-drawn illustration", "clip art", "an anime drawing"]

# category → (review threshold, fail threshold) on the category's summed softmax probability
CLIP_THRESHOLDS = {"nudity": (0.30, 0.45), "swimwear": (0.30, 0.50), "weapons": (0.35, 0.60), "violence": (0.35, 0.60),
                   "alcohol": (0.35, 0.60), "drugs": (0.35, 0.60), "horror": (0.35, 0.60)}
PHOTO_REVIEW, PHOTO_FAIL = 0.35, 0.60            # illustration-only shots: photo-style probability
NUDE_EXPOSED = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "BUTTOCKS_EXPOSED",
                "ANUS_EXPOSED"}
NUDE_COVERED = {"FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED", "ANUS_COVERED"}
NUDE_EXPOSED_REVIEW, NUDE_EXPOSED_FAIL, NUDE_COVERED_REVIEW = 0.60, 0.65, 0.60
FLICKER_CATEGORIES = ("weapons", "violence", "alcohol", "drugs", "horror")   # a REVIEW-level hit in one video frame
                                  # only is a misreading flicker; a real object stays on screen. Nudity/swimwear excluded
NUDITY_CLIP_ALONE_FAIL = 0.70     # CLIP alone fails nudity only when very sure; otherwise it needs NudeNet to agree
COVERED_NEEDS_CLIP = 0.15         # NudeNet "…_COVERED" fires on any clothed chest (a buttoned shirt); it only asks for a
                                  # review when CLIP also sees revealing clothing (its swimwear/lingerie category)

DEFAULT_ILLUSTRATION_SUBJECTS = [
    "swim", "swimming", "swimmer", "swimsuit", "swimwear", "bikini", "beach", "beachwear", "poolside", "swimming pool",
    "pool party", "diving board", "springboard diving", "scuba", "water polo", "aquatic", "aquatics", "surfing",
    "surfer", "lifeguard", "sunbathing",
]

PASS, REVIEW, FAIL = "PASS", "REVIEW", "FAIL"
_RANK = {PASS: 0, REVIEW: 1, FAIL: 2}


def worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _RANK[s]) if statuses else PASS


# ── Small utilities ───────────────────────────────────────────────────────────
def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


_HASH_CACHE: dict = {}


def file_hash(path: str | Path) -> str:
    """SHA-256 of a file, memoised on (path, size, mtime) so render stages can re-check cheaply."""
    p = Path(path)
    st = p.stat()
    k = (str(p.resolve()), st.st_size, int(st.st_mtime))
    if k not in _HASH_CACHE:
        _HASH_CACHE[k] = _sha256(p)
    return _HASH_CACHE[k]


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in (POLICY_VERSION, *parts)).encode("utf-8")).hexdigest()[:20]


def media_key(path, illustration_only: bool = False, window=None) -> str:
    w = f"{float(window[0]):.2f}+{float(window[1]):.2f}" if window else "all"
    return _key("media", file_hash(path), int(bool(illustration_only)), w)


def text_key(text: str) -> str:
    return _key("text", unicodedata.normalize("NFC", (text or "").strip()))


def audio_key(path) -> str:
    return _key("audio", file_hash(path))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Setup / dependency checks ─────────────────────────────────────────────────
def missing_packages() -> list:
    return package_problems()[0]


def package_problems() -> tuple[list, list]:
    """(missing, blocked). Blocked = installed, but Windows Application Control / Smart App Control
    refuses to load its DLLs — reinstalling will not help, so it must not be reported as 'missing'."""
    missing, blocked = [], []
    for mod, pipname in PACKAGES.items():
        try:
            __import__(mod)
        except Exception as e:
            msg = str(e)
            (blocked if ("Application Control" in msg or "DLL load failed" in msg) else missing).append(pipname)
    if not any(_importable(m) for m in OCR_MODULES):
        missing.append("rapidocr")
    return missing, blocked


def _blocked_hint(blocked: list) -> str:
    return (f"{', '.join(blocked)} is installed but Windows is blocking its DLLs (Smart App Control / Application "
            f"Control — see Windows Security > App & browser control). Reinstalling will not fix this.")


def _importable(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def load_ocr():
    """Return read(image_path) → [(text, score)] using whichever RapidOCR package is installed."""
    import logging
    try:
        from rapidocr import RapidOCR                      # 3.x
        try:
            eng = RapidOCR(params={"Global.log_level": "error"})   # its config resets the logger at start-up
        except TypeError:
            eng = RapidOCR()
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)

        def read(path: str) -> list:
            r = eng(path)
            return list(zip(r.txts or (), r.scores or ()))
        return read
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR          # 1.x
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        eng = RapidOCR()

        def read(path: str) -> list:
            res, _ = eng(path)
            return [(r[1], float(r[2])) for r in (res or [])]
        return read


def missing_models(mdir: Path | None = None) -> list:
    mdir = mdir or models_dir()
    out = []
    c = mdir / MODELS["clip"]["dir"]
    for name in MODELS["clip"]["files"]:
        if not (c / name).exists():
            out.append(f"clip/{name}")
    w = mdir / MODELS["whisper"]["dir"]
    for name in MODELS["whisper"]["keep"]:
        if not (w / name).exists():
            out.append(f"whisper/{name}")
    return out


def readiness(project: dict | None = None) -> list:
    """Human-readable blockers; empty list = the gate can run."""
    issues = []
    pk, blocked = package_problems()
    if pk:
        issues.append(f"Python packages missing: {', '.join(pk)} — pip install -r requirements.txt")
    if blocked:
        issues.append(_blocked_hint(blocked))
    mm = missing_models(models_dir(project))
    if mm:
        issues.append(f"Safety models missing in {models_dir(project)}: {', '.join(mm)} — run: python content_safety.py --setup")
    return issues


def _download(url: str, dest: Path, sha: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and _sha256(dest) == sha:
        print(f"  ✅  {dest.name} already present")
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  ↓   {dest.name}  ←  {url}")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "UniqBrio-SVOS/2.0"}), timeout=60) as r, \
            open(tmp, "wb") as f:
        total, got, last = int(r.headers.get("Content-Length") or 0), 0, 0.0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if total and time.time() - last > 2:
                last = time.time()
                print(f"      {got / 1048576:,.0f} / {total / 1048576:,.0f} MB", end="\r")
    digest = _sha256(tmp)
    if digest != sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {dest.name}: got {digest}, expected {sha}")
    tmp.replace(dest)
    print(f"  ✅  {dest.name} verified ({got / 1048576:,.0f} MB)          ")


def setup(mdir: Path) -> int:
    print(f"\n  Content-safety models → {mdir}\n")
    pk, blocked = package_problems()
    if pk:
        print(f"  ❌  Install the Python packages first: pip install -r requirements.txt   (missing: {', '.join(pk)})")
    if blocked:
        print(f"  ❌  {_blocked_hint(blocked)}")
    if pk or blocked:
        return 4
    c = mdir / MODELS["clip"]["dir"]
    for name, (url, sha) in MODELS["clip"]["files"].items():
        _download(url, c / name, sha)
    w = mdir / MODELS["whisper"]["dir"]
    if all((w / k).exists() for k in MODELS["whisper"]["keep"]):
        print("  ✅  Whisper small already present")
    else:
        url, sha = MODELS["whisper"]["archive"]
        arc = mdir / "whisper-small.tar.bz2"
        _download(url, arc, sha)
        print("  📦  unpacking Whisper small (int8 files only)…")
        with tarfile.open(arc, "r:bz2") as tf:
            for m in tf.getmembers():
                if Path(m.name).name in MODELS["whisper"]["keep"]:
                    m.name = Path(m.name).name
                    try:
                        tf.extract(m, w, filter="data")      # Python 3.12+: refuse links / absolute paths
                    except TypeError:
                        tf.extract(m, w)
        arc.unlink(missing_ok=True)
    # NudeNet and RapidOCR ship their models inside the pip packages; load them once to prove it.
    from nudenet import NudeDetector  # noqa: F401
    load_ocr()
    left = missing_models(mdir)
    if left:
        print(f"  ❌  still missing: {left}")
        return 4
    print("\n  ✅  Content-safety models ready\n")
    return 0


# ── Text check (blocklist) ────────────────────────────────────────────────────
_TAMIL = re.compile(r"[஀-௿]")
_LEET = str.maketrans({"@": "a", "4": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s", "5": "s", "7": "t"})


class Blocklist:
    def __init__(self, files: list[Path] | None = None):
        files = files or [HERE / "safety" / "blocklist.txt", HERE / "safety" / "blocklist_extra.txt"]
        self.rules = []                       # (level, category, term, compiled regex)
        for f in files:
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 2)
                if len(parts) != 3 or parts[0] not in ("fail", "review"):
                    continue
                level, cat, term = parts
                self.rules.append((level, cat, term, self._compile(term)))

    @staticmethod
    def _compile(term: str):
        term = unicodedata.normalize("NFC", term.strip().lower())
        wild = term.endswith("*")
        core = re.escape(term[:-1] if wild else term).replace(r"\ ", r"[\s\-_]+")
        if _TAMIL.search(term):
            tail = r"[஀-௿]*" if wild else r"(?![஀-௿])"
            return re.compile(r"(?<![஀-௿])" + core + tail)
        tail = r"\w*" if wild else r"\b"
        return re.compile(r"\b" + core + tail, re.IGNORECASE)

    def check(self, text: str) -> tuple[str, list]:
        if not text or not text.strip():
            return PASS, []
        base = unicodedata.normalize("NFC", text).lower()
        variants = {base, base.translate(_LEET), re.sub(r"(?<=\w)[.\-_*](?=\w)", "", base)}
        status, hits = PASS, []
        for level, cat, term, rx in self.rules:
            for v in variants:
                m = rx.search(v)
                if m:
                    hits.append(f"{cat}: “{m.group(0)}” ({level})")
                    status = worst(status, FAIL if level == "fail" else REVIEW)
                    break
        return status, sorted(set(hits))


_BLOCKLIST: Blocklist | None = None


def check_text(text: str) -> tuple[str, list]:
    global _BLOCKLIST
    if _BLOCKLIST is None:
        _BLOCKLIST = Blocklist()
    return _BLOCKLIST.check(text)


def subject_needs_illustration(texts: list[str], subjects: list[str] | None = None) -> str:
    """Return the matched subject word if any text mentions a swimwear-type subject, else ''."""
    subjects = subjects or DEFAULT_ILLUSTRATION_SUBJECTS
    blob = " ".join(t for t in texts if t).lower()
    for s in subjects:
        s = s.strip().lower()
        if s and re.search(r"\b" + re.escape(s).replace(r"\ ", r"\s+") + r"(s|es|ers?|ing)?\b", blob):
            return s
    return ""


# ── Models (lazy) ─────────────────────────────────────────────────────────────
class Clip:
    MEAN = (0.48145466, 0.4578275, 0.40821073)
    STD = (0.26862954, 0.26130258, 0.27577711)

    def __init__(self, mdir: Path):
        import numpy as np
        import onnxruntime as ort
        sys.path.insert(0, str(HERE / "safety"))
        from clip_tokenizer import Tokenizer
        self.np = np
        d = mdir / MODELS["clip"]["dir"]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.vis = ort.InferenceSession(str(d / "visual.onnx"), so, providers=["CPUExecutionProvider"])
        txt = ort.InferenceSession(str(d / "textual.onnx"), so, providers=["CPUExecutionProvider"])
        tok = Tokenizer()

        def embed(prompts):
            ids = tok.encode_batch(prompts)
            e = txt.run(None, {"input_ids": ids, "attention_mask": (ids != 0).astype(np.int32)})[0]
            return e / np.linalg.norm(e, axis=1, keepdims=True)

        self.labels = [(c, p) for c, ps in UNSAFE_PROMPTS.items() for p in ps] + [("safe", p) for p in SAFE_PROMPTS]
        self.T = embed([p for _, p in self.labels])
        self.S = embed(PHOTO_PROMPTS + ILLU_PROMPTS)
        self.mean = np.array(self.MEAN, np.float32)
        self.std = np.array(self.STD, np.float32)

    def _square(self, im):
        from PIL import Image
        im = im.resize((224, 224), Image.BICUBIC)
        a = (self.np.asarray(im, self.np.float32) / 255 - self.mean) / self.std
        return a.transpose(2, 0, 1)

    def crops(self, im):
        """Letterboxed whole frame + square tiles along the long side (a 9:16 frame would lose its top and bottom to a centre crop)."""
        from PIL import Image
        im = im.convert("RGB")
        w, h = im.size
        s = max(w, h)
        box = Image.new("RGB", (s, s), (0, 0, 0))
        box.paste(im, ((s - w) // 2, (s - h) // 2))
        out = [self._square(box)]
        short, long_ = min(w, h), max(w, h)
        n = 1 if long_ / short < 1.25 else 3
        for i in range(n):
            off = int((long_ - short) * (i / (n - 1) if n > 1 else 0.5))
            c = im.crop((0, off, w, off + short)) if h > w else im.crop((off, 0, off + short, h))
            out.append(self._square(c))
        return out

    def score(self, im) -> tuple[dict, float]:
        np = self.np
        x = np.stack(self.crops(im)).astype(np.float32)
        e = self.vis.run(None, {"pixel_values": x})[0]
        e /= np.linalg.norm(e, axis=1, keepdims=True)
        z = 100.0 * (e @ self.T.T)
        p = np.exp(z - z.max(axis=1, keepdims=True))
        p /= p.sum(axis=1, keepdims=True)
        per_crop = []
        for row in p:
            d: dict = {}
            for j, (c, _) in enumerate(self.labels):
                if c != "safe":
                    d[c] = d.get(c, 0.0) + float(row[j])
            per_crop.append(d)
        cats = {c: max(d[c] for d in per_crop) for c in per_crop[0]}
        zs = 100.0 * (e @ self.S.T)
        ps = np.exp(zs - zs.max(axis=1, keepdims=True))
        ps /= ps.sum(axis=1, keepdims=True)
        photo = float(ps[:, :len(PHOTO_PROMPTS)].sum(axis=1).max())
        return cats, photo


class Engines:
    """Lazily loaded model bundle shared by every check in one run."""

    def __init__(self, mdir: Path, whisper_threads: int = 4):
        self.mdir = mdir
        self._clip = self._nude = self._ocr = self._asr = None
        self.threads = whisper_threads

    @property
    def clip(self) -> Clip:
        if self._clip is None:
            self._clip = Clip(self.mdir)
        return self._clip

    @property
    def nude(self):
        if self._nude is None:
            from nudenet import NudeDetector
            self._nude = NudeDetector()
        return self._nude

    @property
    def ocr(self):
        if self._ocr is None:
            self._ocr = load_ocr()
        return self._ocr

    def asr(self, language: str):
        if self._asr is None:
            self._asr = {}
        if language not in self._asr:
            sys.path.insert(0, str(HERE / "safety"))
            from whisper_onnx import WhisperOnnx
            self._asr[language] = WhisperOnnx(self.mdir / MODELS["whisper"]["dir"], prefix="small",
                                              language=language, threads=self.threads)
        return self._asr[language]


# ── Media helpers ────────────────────────────────────────────────────────────
def _probe_duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=30)
        return float(json.loads(r.stdout or "{}").get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".gif"}


def extract_frames(path: Path, out_dir: Path, window=None, fps: float = 1.0, max_frames: int = 24) -> list[Path]:
    """Sample frames (≤ 768 px) from a video window, or return a flattened copy of a still."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not _is_video(path):
        from PIL import Image
        dest = out_dir / "still.jpg"
        with Image.open(path) as im:
            im.seek(0)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (128, 128, 128))     # neutral ground for transparent logos
                bg.paste(im, mask=im.split()[-1])
                im = bg
            im = im.convert("RGB")
            im.thumbnail((768, 768))
            im.save(dest, "JPEG", quality=90)
        return [dest]
    start, dur = (float(window[0]), float(window[1])) if window else (0.0, _probe_duration(path))
    dur = max(dur, 0.5)
    n = int(min(max_frames, max(3, round(dur * fps))))
    rate = n / dur
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
                    "-vf", f"fps={rate:.4f},scale='min(768,iw)':-2", "-frames:v", str(n), "-q:v", "3",
                    str(out_dir / "f_%03d.jpg")], capture_output=True, timeout=600)
    frames = sorted(out_dir.glob("f_*.jpg"))
    if not frames:                                              # very short clip: take the first frame
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1", str(out_dir / "f_001.jpg")],
                       capture_output=True, timeout=120)
        frames = sorted(out_dir.glob("f_*.jpg"))
    return frames


def judge_frame(eng: Engines, frame: Path, illustration_only: bool, do_ocr: bool, card: bool = False) -> dict:
    from PIL import Image
    with Image.open(frame) as im:
        cats, photo = eng.clip.score(im)
    status, reasons = PASS, []
    faces, exposed = 0, 0.0
    try:
        dets = eng.nude.detect(str(frame))
    except Exception as e:                                      # never let a detector error pass silently
        dets = []
        status = worst(status, REVIEW); reasons.append(f"nudity detector error: {e}")
    for d in dets:
        if d.get("class", "") in NUDE_EXPOSED:
            exposed = max(exposed, float(d.get("score", 0)))
    for cat, p in cats.items():
        rv, fl = CLIP_THRESHOLDS[cat]
        if card:
            continue      # T1/T5 card: flat colour + separately checked text/logo — CLIP reads letters as objects
        if cat == "swimwear" and illustration_only and photo < PHOTO_REVIEW:
            continue                                            # cartoon swimwear is the allowed treatment here
        if cat == "nudity" and p >= fl and p < NUDITY_CLIP_ALONE_FAIL and exposed < 0.4:
            status = worst(status, REVIEW); reasons.append(f"possible nudity {p:.2f} (second detector disagrees)")
        elif p >= fl:
            status = worst(status, FAIL); reasons.append(f"{cat} {p:.2f}")
        elif p >= rv:
            status = worst(status, REVIEW); reasons.append(f"possible {cat} {p:.2f}")
    for d in dets:
        cls, sc = d.get("class", ""), float(d.get("score", 0))
        if cls.startswith("FACE_") and sc >= 0.5:
            faces += 1
        if cls in NUDE_EXPOSED and sc >= NUDE_EXPOSED_FAIL:
            status = worst(status, FAIL); reasons.append(f"nudity detector: {cls.lower()} {sc:.2f}")
        elif cls in NUDE_EXPOSED and sc >= NUDE_EXPOSED_REVIEW:
            status = worst(status, REVIEW); reasons.append(f"nudity detector (possible): {cls.lower()} {sc:.2f}")
        elif (cls in NUDE_COVERED and sc >= NUDE_COVERED_REVIEW and cats.get("swimwear", 0) >= COVERED_NEEDS_CLIP
              and not (illustration_only and photo < PHOTO_REVIEW)):
            status = worst(status, REVIEW); reasons.append(f"revealing clothing? {cls.lower()} {sc:.2f}")
    if illustration_only:
        if photo >= PHOTO_FAIL:
            status = worst(status, FAIL)
            reasons.append(f"real photo/video ({photo:.2f}) in a swimwear-subject shot — use cartoon/illustration only")
        elif photo >= PHOTO_REVIEW:
            status = worst(status, REVIEW); reasons.append(f"may be a real photo ({photo:.2f}) — must be cartoon/illustration")
        if faces and photo >= PHOTO_REVIEW:
            status = worst(status, FAIL); reasons.append("identifiable real face in a swimwear-subject shot")
    text = ""
    if do_ocr:
        try:
            text = " ".join(t for t, sc in eng.ocr(str(frame)) if float(sc) >= 0.6)
        except Exception as e:                             # an unread frame is not a clean frame
            text = ""
            status = worst(status, REVIEW); reasons.append(f"text in frame could not be read ({e.__class__.__name__})")
        if text:
            ts, hits = check_text(text)
            if ts != PASS:
                status = worst(status, ts); reasons += [f"visible text — {h}" for h in hits]
    return {"frame": str(frame), "status": status, "reasons": reasons, "scores": {k: round(v, 3) for k, v in cats.items()},
            "photo": round(photo, 3), "faces": faces, "ocr": text}


_FLICKER_RE = re.compile(r"^possible (" + "|".join(FLICKER_CATEGORIES) + r") [0-9.]+$")


def _drop_flicker(results: list) -> list:
    """In a video (≥ 3 frames), a REVIEW-level CLIP hit for an object category seen in ONE frame only is
    dropped (and noted); two or more frames, or any FAIL-level hit, still counts."""
    if len(results) < 3:
        return []
    count = {}
    for r in results:
        for x in {re.sub(r" [0-9.]+$", "", x) for x in r["reasons"] if _FLICKER_RE.match(x)}:
            count[x] = count.get(x, 0) + 1
    notes = []
    for r in results:
        keep = []
        for x in r["reasons"]:
            if _FLICKER_RE.match(x) and count.get(re.sub(r" [0-9.]+$", "", x), 0) < 2:
                notes.append(f"ignored one-frame flicker: {x} (1 of {len(results)} frames)")
            else:
                keep.append(x)
        if len(keep) < len(r["reasons"]) and r["status"] == REVIEW and not keep:
            r["status"] = PASS
        r["reasons"] = keep
    return notes


def judge_media(eng: Engines, path: Path, work: Path, illustration_only=False, window=None, fps=1.0,
                max_frames=24, ocr_every=2, card: bool = False) -> dict:
    frames = extract_frames(path, work, window, fps, max_frames)
    if not frames:
        return {"status": REVIEW, "reasons": ["could not read any frame — check the file"], "frames": []}
    results = [judge_frame(eng, f, illustration_only, do_ocr=(i % max(1, ocr_every) == 0), card=card)
               for i, f in enumerate(frames)]
    notes = _drop_flicker(results)
    status = worst(*(r["status"] for r in results))
    reasons, seen = [], set()
    for r in results:
        for x in r["reasons"]:
            base = re.sub(r"\s[0-9.]+$", "", x)
            if base not in seen:
                seen.add(base); reasons.append(x)
    flagged = [r for r in results if r["status"] != PASS][:6]
    return {"status": status, "reasons": reasons, "frames": flagged or results[:1], "notes": notes,
            "max_photo": max(r["photo"] for r in results), "n_frames": len(results),
            "ocr": " / ".join(sorted({r["ocr"] for r in results if r["ocr"]}))[:600]}


def transcribe(eng: Engines, path: Path, languages: list[str], window=None) -> dict:
    """Whisper transcript per language (chunked at 28 s). '' language = auto-detect."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error"]
    if window:
        cmd += ["-ss", f"{float(window[0]):.3f}", "-t", f"{float(window[1]):.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    audio = np.frombuffer(raw, np.float32)
    out = {}
    if audio.size < 1600 or float(np.abs(audio).max(initial=0)) < 1e-3:
        return {lang or "auto": "" for lang in languages}
    step = 28 * 16000
    for lang in languages:
        rec = eng.asr(lang)
        parts = []
        for i in range(0, audio.size, step):
            chunk = audio[i:i + step]
            if chunk.size < 8000 or float(np.abs(chunk).max()) < 1e-3:
                continue
            parts.append(rec.transcribe(chunk))
        out[lang or "auto"] = " ".join(p for p in parts if p)
    return out


# ── Item enumeration ──────────────────────────────────────────────────────────
def media_window(shot: dict):
    """The part of a shot's source that is actually used — the same window the gate checks and the renderer uses."""
    lf = shot.get("local_file", "")
    if lf and _is_video(Path(lf)):
        return (float(shot.get("trim_in", 0) or 0), float(shot.get("duration", 4.0)))
    return None

def _languages(project: dict) -> list[str]:
    lang = (project.get("safety", {}).get("vo_language") or "auto").lower()
    has_tamil = any(_TAMIL.search(" ".join(str(s.get(k, "")) for k in ("text_overlay", "vo_line")))
                    for s in project.get("shots", []))
    if lang in ("en", "ta"):
        return [lang]
    return ["", "ta"] if has_tamil else [""]


def asset_items(project: dict) -> list[dict]:
    """Everything that can reach the video, before rendering."""
    from svos_common import shot_kind
    items, seen = [], set()

    def add(item):
        k = (item["kind"], item.get("path") or item.get("text"), item.get("illustration_only"), str(item.get("window")))
        if k not in seen:
            seen.add(k); items.append(item)

    for s in project.get("shots", []):
        sid, kind = s["shot_id"], shot_kind(s)
        illu = bool(s.get("illustration_only"))
        lf = s.get("local_file", "")
        if kind in ("stock", "product") and lf:
            window = media_window(s)
            role = "ai-generated" if s.get("source") == "ai-generated" else ("stock" if s.get("source") in
                                                                             ("pexels", "pixabay", "unsplash") else
                                                                             ("product" if kind == "product" else "your file"))
            add({"kind": "media", "role": role, "shot_id": sid, "label": f"Shot {sid} {role}", "path": lf,
                 "illustration_only": illu, "window": window})
        if kind == "logo_card":
            logo = s.get("logo_file") or lf or project.get("logo", {}).get("path", "")
            if logo:
                add({"kind": "media", "role": "logo", "shot_id": sid, "label": f"Shot {sid} logo card",
                     "path": logo, "illustration_only": False, "window": None})
        if s.get("cta_chip"):
            add({"kind": "text", "role": "on-screen text", "shot_id": sid, "label": f"Shot {sid} CTA chip",
                 "text": s["cta_chip"]["text"]})
        if s.get("text_overlay") and kind != "product":
            add({"kind": "text", "role": "on-screen text", "shot_id": sid, "label": f"Shot {sid} on-screen text",
                 "text": s["text_overlay"]})
        if s.get("vo_line"):
            add({"kind": "text", "role": "voiceover script", "shot_id": sid, "label": f"Shot {sid} VO line",
                 "text": s["vo_line"]})
        if s.get("vo_file") and Path(s["vo_file"]).exists():
            add({"kind": "audio", "role": "voiceover", "shot_id": sid, "label": f"Shot {sid} VO file", "path": s["vo_file"]})
    logo = project.get("logo", {}).get("path", "")
    if logo:
        add({"kind": "media", "role": "logo", "shot_id": "", "label": "Logo", "path": logo, "illustration_only": False,
             "window": None})
        if (project.get("logo", {}).get("mode") or "").lower() in ("bug", "both") and Path(logo).exists():
            from svos_common import logo_variants      # the corner bug picks a variant after seeing the footage
            for v in logo_variants(logo)[1:]:
                add({"kind": "media", "role": "logo", "shot_id": "", "label": f"Logo variant {Path(v).name}",
                     "path": v, "illustration_only": False, "window": None})
    for k, role in (("vo_path", "voiceover"), ("music_path", "music"), ("bgm_path", "background music"),
                    ("sting_path", "end sting")):
        p = project.get("audio", {}).get(k, "")
        if p:
            add({"kind": "audio", "role": role, "shot_id": "", "label": role.capitalize(), "path": p})
    cap = project.get("safety", {}).get("captions_path", "")
    if cap:
        add({"kind": "captions", "role": "captions", "shot_id": "", "label": "Captions", "path": cap})
    return items


def _caption_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln.isdigit() or "-->" in ln or ln.upper().startswith("WEBVTT"):
            continue
        lines.append(re.sub(r"<[^>]+>", "", ln))
    return "\n".join(lines)


# ── Evaluation ────────────────────────────────────────────────────────────────
def evaluate(item: dict, eng: Engines | None, work: Path, cache: Path, project: dict) -> dict:
    kind = item["kind"]
    res = dict(item)
    try:
        if kind == "text":
            res["key"] = text_key(item["text"])
        elif kind in ("media",):
            p = Path(item["path"])
            if not p.exists():
                res.update(status=FAIL, reasons=[f"file not found: {p}"], key=_key("missing", str(p)))
                return res
            res["key"] = media_key(p, item.get("illustration_only"), item.get("window"))
            if item.get("card"):
                res["key"] = _key("card", res["key"])
        elif kind in ("audio", "captions"):
            p = Path(item["path"])
            if not p.exists():
                res.update(status=FAIL, reasons=[f"file not found: {p}"], key=_key("missing", str(p)))
                return res
            res["key"] = audio_key(p) if kind == "audio" else _key("captions", file_hash(p))
        cf = cache / f"{res['key']}.json"
        if cf.exists():
            cached = json.loads(cf.read_text(encoding="utf-8"))
            res.update({k: v for k, v in cached.items() if k in ("status", "reasons", "frames", "transcript", "ocr",
                                                                 "max_photo", "n_frames")})
            res["cached"] = True
            return res
        if kind == "text":
            st, hits = check_text(item["text"])
            res.update(status=st, reasons=hits)
        elif kind == "captions":
            txt = _caption_text(Path(item["path"]))
            st, hits = check_text(txt)
            res.update(status=st, reasons=hits, transcript=txt[:4000])
        elif kind == "media":
            cfg = project.get("safety", {})
            r = judge_media(eng, Path(item["path"]), work / res["key"], item.get("illustration_only", False),
                            item.get("window"), float(item.get("fps") or cfg.get("frames_per_second") or 1.0),
                            int(item.get("max_frames") or cfg.get("max_frames_per_asset") or 24),
                            card=bool(item.get("card")))
            res.update(r)
        elif kind == "audio":
            tr = transcribe(eng, Path(item["path"]), _languages(project))
            st, reasons = PASS, []
            for lang, txt in tr.items():
                s2, hits = check_text(txt)
                st = worst(st, s2)
                reasons += [f"spoken ({lang}) — {h}" for h in hits]
            if any(tr.values()) and "ta" in tr:
                reasons.append("Tamil speech: the automatic transcript is approximate — listen before signing off")
            res.update(status=st, reasons=reasons, transcript=json.dumps(tr, ensure_ascii=False))
        cache.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({k: res.get(k) for k in ("status", "reasons", "frames", "transcript", "ocr",
                                                          "max_photo", "n_frames")}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        res.update(status=REVIEW, reasons=[f"check could not run ({e.__class__.__name__}: {e}) — review by hand"])
        res.setdefault("key", _key("error", json.dumps({k: str(v) for k, v in item.items()})))
    return res


def _state(project: dict) -> dict:
    st = project.setdefault("safety_state", {})
    for k in ("approvals", "rejections", "overrides", "cleared"):
        st.setdefault(k, {})
    return st


def resolve(project: dict, results: list[dict]) -> tuple[str, list, list]:
    """Apply recorded human decisions. Returns (overall, blocking_fail, needs_review)."""
    st = _state(project)
    fails, reviews = [], []
    for r in results:
        k = r["key"]
        r["decision"] = ""
        if k in st["overrides"]:
            r["decision"] = "override"
        elif k in st["rejections"]:
            r["decision"] = "rejected"
            fails.append(r)
            continue
        elif r["status"] == REVIEW and k in st["approvals"]:
            r["decision"] = "approved"
        if r["status"] == FAIL and r["decision"] != "override":
            fails.append(r)
        elif r["status"] == REVIEW and r["decision"] not in ("approved", "override"):
            reviews.append(r)
    overall = FAIL if fails else REVIEW if reviews else PASS
    return overall, fails, reviews


# ── Report ────────────────────────────────────────────────────────────────────
def _thumb(path: str) -> str:
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((220, 390))
            b = BytesIO()
            im.save(b, "JPEG", quality=75)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:
        return ""


def write_report(project: dict, stage: str, results: list[dict], out_html: Path, extra: dict) -> None:
    name = html.escape(project.get("project_name", "Video"))
    overall, fails, reviews = resolve(project, results)
    colour = {PASS: "#27ae60", REVIEW: "#e67e22", FAIL: "#c0392b"}
    rows = ""
    for r in sorted(results, key=lambda r: (-_RANK[r["status"]], r.get("label", ""))):
        imgs = "".join(f'<img src="{_thumb(f["frame"])}" title="{html.escape("; ".join(f.get("reasons", [])))}">'
                       for f in (r.get("frames") or []) if f.get("frame"))
        content = ""
        if r["kind"] == "text":
            content = f'<div class="txt">{html.escape(r["text"])}</div>'
        if r.get("transcript"):
            try:
                tr = json.loads(r["transcript"])
                content += "".join(f'<div class="txt"><b>{html.escape(k)}:</b> {html.escape(v) or "<i>(no speech)</i>"}</div>'
                                   for k, v in tr.items())
            except Exception:
                content += f'<div class="txt">{html.escape(r["transcript"][:1500])}</div>'
        if r.get("ocr"):
            content += f'<div class="txt ocr">Text seen in frames: {html.escape(r["ocr"])}</div>'
        dec = r.get("decision", "")
        if r["status"] == REVIEW and dec not in ("approved", "override", "rejected"):
            ctl = (f'<label><input type="radio" name="{r["key"]}" value="approve" onchange="upd()"> Approve — suitable for a U audience</label>'
                   f'<label><input type="radio" name="{r["key"]}" value="reject" onchange="upd()"> Reject — replace it</label>')
        elif r["status"] == FAIL and dec != "override":
            ctl = ('<span class="must">Must be replaced.</span>' if r.get("role") in ("stock", "ai-generated")
                   else '<span class="must">Replace this file.</span>')
            ctl += f'<label><input type="radio" name="{r["key"]}" value="reject" onchange="upd()"> Mark rejected</label>'
        else:
            ctl = f'<span class="ok">{html.escape(dec or "cleared")}</span>'
        rows += f"""<section class="it" style="border-left-color:{colour[r['status']]}">
  <div class="hd"><b style="color:{colour[r['status']]}">{r['status']}</b> · {html.escape(r.get('label', ''))}
   <span class="role">{html.escape(r.get('role', ''))}{' · cartoon-only shot' if r.get('illustration_only') else ''}</span></div>
  {'<ul>' + ''.join(f'<li>{html.escape(x)}</li>' for x in r.get('reasons', [])) + '</ul>' if r.get('reasons') else ''}
  {content}<div class="imgs">{imgs}</div><div class="ctl">{ctl}</div><div class="key">{r['key']}</div></section>"""
    signoff = ""
    if stage == "final":
        signoff = f"""<section class="so"><h2>Human sign-off (required before publishing)</h2>
  <p>Final video: <code>{html.escape(extra.get('video', ''))}</code><br>SHA-256 <code>{extra.get('video_sha', '')[:16]}…</code></p>
  <label><input type="checkbox" class="chk"> I watched the whole video with sound ON and saw nothing unsuitable for children</label>
  <label><input type="checkbox" class="chk"> I read every on-screen text, caption and the transcript above</label>
  <label><input type="checkbox" class="chk"> Shots marked cartoon-only use cartoon / illustrated visuals and no real or celebrity faces</label>
  <label>Reviewer name <input id="who" oninput="upd()"></label></section>"""
    doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Content safety — {name} ({stage})</title><style>
body{{font-family:-apple-system,Segoe UI,sans-serif;background:#0f0f17;color:#e8e4dc;margin:0;padding:22px;line-height:1.5}}
h1{{color:#DE7D14;font-size:22px;margin:0}} .sub{{color:#999;font-size:13px;margin:4px 0 18px}}
.big{{font-size:18px;font-weight:700;color:{colour[overall]}}}
.it{{background:#1a1a2e;border:1px solid #2a2a40;border-left:6px solid;border-radius:10px;padding:12px 16px;margin:10px 0}}
.hd{{font-size:14px}} .role{{color:#888;font-size:12px;margin-left:8px}} ul{{margin:6px 0 6px 18px;font-size:13px;color:#f0c8a0}}
.txt{{background:#111120;border-radius:6px;padding:6px 10px;margin:6px 0;font-size:14px;white-space:pre-wrap}} .ocr{{color:#aaa;font-size:12px}}
.imgs img{{height:160px;margin:4px 6px 0 0;border-radius:6px}} .ctl label{{display:inline-block;margin:8px 18px 0 0;font-size:13px}}
.must{{color:#c0392b;font-weight:700;margin-right:14px}} .ok{{color:#27ae60;font-size:13px}} .key{{color:#444;font-size:10px}}
.so{{background:#10200f;border:1px solid #27ae60;border-radius:10px;padding:14px 18px;margin-top:20px}} .so label{{display:block;margin:6px 0}}
#bar{{position:sticky;bottom:0;background:#0a0a12;border-top:1px solid #333;padding:12px 0;margin-top:20px}}
button{{background:#DE7D14;color:#fff;border:none;padding:9px 18px;border-radius:6px;font-weight:600;cursor:pointer}}
</style></head><body>
<h1>🛡 Content safety — {name}</h1>
<div class="sub">Stage: {stage} · policy {POLICY_VERSION} · {datetime.now():%d %b %Y %H:%M} · local models only</div>
<div class="big">{overall} — {len(fails)} must be replaced · {len(reviews)} need your decision · {len(results)} items checked</div>
{rows}{signoff}
<div id="bar"><button onclick="save()">💾 Save safety_review.json</button> <span id="msg" style="color:#aaa;font-size:13px"></span>
<div style="color:#888;font-size:12px;margin-top:6px">Then run: <code>python run_pipeline.py {html.escape(Path(project.get('source_xlsx') or 'story_plan.xlsx').name)} --apply-safety-review &lt;path to safety_review.json&gt;</code></div></div>
<script>
const STAGE={json.dumps(stage)}, VIDEO_SHA={json.dumps(extra.get('video_sha', ''))};
function collect(){{const items={{}};document.querySelectorAll('input[type=radio]:checked').forEach(r=>items[r.name]=r.value);
  const out={{stage:STAGE,policy:{json.dumps(POLICY_VERSION)},items:items}};
  if(STAGE==='final'){{const all=[...document.querySelectorAll('.chk')].every(c=>c.checked);const who=(document.getElementById('who')||{{}}).value||'';
    if(all&&who.trim())out.signoff={{video_sha:VIDEO_SHA,name:who.trim(),confirmed:[...document.querySelectorAll('.chk')].map(c=>c.parentElement.textContent.trim()),at:new Date().toISOString()}};}}
  return out;}}
function upd(){{const o=collect();document.getElementById('msg').textContent=Object.keys(o.items).length+' decision(s)'+(o.signoff?' · sign-off ready':'');}}
function save(){{const o=collect();if(STAGE==='final'&&!o.signoff&&!Object.keys(o.items).length){{alert('Tick all three boxes and enter your name to sign off, or record decisions.');return;}}
  const b=new Blob([JSON.stringify(o,null,2)],{{type:'application/json'}});const a=document.createElement('a');
  a.href=URL.createObjectURL(b);a.download='safety_review.json';document.body.appendChild(a);a.click();a.remove();}}
upd();
</script></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")


# ── Stages ────────────────────────────────────────────────────────────────────
def _paths(project: dict, proj_path: Path) -> tuple[Path, Path, Path]:
    from svos_common import output_dir, assets_dir
    out = output_dir(project, proj_path) / "safety"
    cache = assets_dir(project, proj_path) / "_cache" / "safety"
    return out, cache, out / "_frames"


def _print(results: list[dict]) -> None:
    icon = {PASS: "✅", REVIEW: "⚠️ ", FAIL: "❌"}
    for r in sorted(results, key=lambda r: (-_RANK[r["status"]], r.get("label", ""))):
        dec = f" [{r['decision']}]" if r.get("decision") else ""
        why = ("  — " + "; ".join(r["reasons"][:3])) if r.get("reasons") and r["status"] != PASS else ""
        print(f"    {icon[r['status']]} {r['status']:<6} {r.get('label', '')}{dec}{why}")


def run_stage(project: dict, proj_path: Path, stage: str, eng: Engines | None = None) -> int:
    from svos_common import save_project
    out, cache, work = _paths(project, proj_path)
    blockers = readiness(project)
    if blockers:
        for b in blockers:
            print(f"  ❌  {b}")
        return 4
    eng = eng or Engines(models_dir(project))
    extra: dict = {}
    if stage == "assets":
        items = asset_items(project)
    else:
        final = Path(project.get("final_output", ""))
        if not final.exists():
            print("  ❌  final_output missing — run final_export first")
            return 1
        extra = {"video": str(final), "video_sha": file_hash(final)}
        dur = _probe_duration(final)
        cfg = project.get("safety", {})
        fps = float(cfg.get("final_frames_per_second", 1.0) or 1.0)
        items = []
        by_id = {x["shot_id"]: x for x in project.get("shots", [])}
        tl = project.get("timeline") or [{"shot_id": "", "start": 0.0, "end": dur, "trans_dur": 0, "kind": "stock"}]
        for t in tl:
            a, b = float(t["start"]), min(float(t["end"]), dur or float(t["end"]))
            if b - a < 0.2:
                continue
            shot = by_id.get(t["shot_id"], {})
            illu = bool(shot.get("illustration_only"))
            card = t.get("kind") in ("card", "logo_card")
            items.append({"kind": "media", "role": "final video", "shot_id": t["shot_id"], "illustration_only": illu,
                          "card": card, "path": str(final), "window": (a, b - a), "fps": fps,
                          "max_frames": int(max(3, (b - a) * fps + 2)),
                          "label": f"Final video — shot {t['shot_id']} ({a:.1f}–{b:.1f}s)"
                                   + (" · cartoon-only" if illu else "") + (" · card" if card else "")})
        items.append({"kind": "audio", "role": "final mix", "shot_id": "", "label": "Final audio (everything a viewer hears)",
                      "path": str(final)})
        for m in project.get("overlay_manifest", []):
            items.append({"kind": "text", "role": "rendered text", "shot_id": m["shot_id"],
                          "label": f"Shot {m['shot_id']} rendered text", "text": " ".join(m.get("lines", []))})
        cap = cfg.get("captions_path", "")
        if cap:
            items.append({"kind": "captions", "role": "captions", "shot_id": "", "label": "Captions", "path": cap})
    print(f"  Checking {len(items)} item(s) with local models (policy {POLICY_VERSION})…")
    t0 = time.time()
    results = []
    for it in items:
        r = evaluate(it, eng, work, cache, project)
        results.append(r)
    overall, fails, reviews = resolve(project, results)
    st = _state(project)
    for r in results:
        if r["status"] == PASS or r.get("decision") in ("approved", "override"):
            st["cleared"][r["key"]] = {"label": r.get("label", ""), "at": _now()}
    # Stock / AI assets that FAIL are rejected automatically so the fetcher replaces them
    auto = auto_reject_failed_assets(project, fails) if stage == "assets" else []
    report = out / f"{stage}_report{_sfx(project)}.html"
    write_report(project, stage, results, report, extra)
    summary = {"stage": stage, "status": overall, "policy": POLICY_VERSION, "checked_at": _now(),
               "fails": len(fails), "reviews": len(reviews), "items": len(results), "report": str(report),
               "results": [{k: r.get(k) for k in ("key", "kind", "role", "label", "status", "reasons", "decision", "shot_id")}
                           for r in results], **extra}
    (out / f"{stage}_report{_sfx(project)}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    project["safety_state"][stage] = {k: summary[k] for k in ("status", "policy", "checked_at", "fails", "reviews",
                                                              "items", "report")} | ({"video_sha": extra["video_sha"]}
                                                                                     if extra else {})
    save_project(project, proj_path)
    _print(results)
    print(f"\n  {'✅' if overall == PASS else '⚠️ ' if overall == REVIEW else '❌'}  {stage.upper()} safety: {overall} "
          f"— {len(fails)} must be replaced · {len(reviews)} need your decision · {len(results)} checked "
          f"({time.time() - t0:.0f}s)\n  📄  {report}")
    if auto:
        print(f"  🔄  Rejected automatically (will be re-fetched): shots {', '.join(auto)}")
    if overall == REVIEW:
        print("  🚦  Open the report, Approve / Reject each flagged item, Save safety_review.json, then\n"
              "      python run_pipeline.py <plan.xlsx> --apply-safety-review <safety_review.json>")
    return {PASS: 0, REVIEW: 3, FAIL: 1}[overall]


def auto_reject_failed_assets(project: dict, fails: list[dict]) -> list[str]:
    from asset_fetcher import reject_current
    by_id = {s["shot_id"]: s for s in project.get("shots", [])}
    done = []
    for r in fails:
        s = by_id.get(r.get("shot_id"))
        if s and r.get("kind") == "media" and r.get("role") in ("stock",) and s.get("local_file") == r.get("path"):
            reject_current(s, "swap")
            s["safety_rejected_reason"] = "; ".join(r.get("reasons", [])[:3])
            done.append(s["shot_id"])
    return done


# ── Human decisions ───────────────────────────────────────────────────────────
def apply_review(project: dict, proj_path: Path, review: dict) -> int:
    from svos_common import save_project
    st = _state(project)
    if review.get("policy") and review["policy"] != POLICY_VERSION:
        print(f"  ⚠️  review file was made under policy {review['policy']} (current {POLICY_VERSION}) — rerun the check first")
        return 1
    known = {}
    out, _, _ = _paths(project, proj_path)
    for stage in ("assets", "final"):
        f = out / f"{stage}_report{_sfx(project)}.json"
        if f.exists():
            for r in json.loads(f.read_text(encoding="utf-8")).get("results", []):
                known[r["key"]] = r
    n_ok = n_rej = 0
    for key, verdict in (review.get("items") or {}).items():
        r = known.get(key)
        if not r:
            print(f"  ⚠️  unknown item {key} — ignored (was the report regenerated?)")
            continue
        if verdict == "approve":
            if r["status"] == FAIL:
                print(f"  ❌  {r['label']}: FAIL items cannot be approved — replace it (or use --override with a reason)")
                continue
            st["approvals"][key] = {"label": r["label"], "at": _now()}
            st["cleared"][key] = {"label": r["label"], "at": _now(), "by": "human"}
            st["rejections"].pop(key, None)
            n_ok += 1
        elif verdict == "reject":
            st["rejections"][key] = {"label": r["label"], "at": _now()}
            st["cleared"].pop(key, None)
            st["approvals"].pop(key, None)
            if r.get("kind") == "media" and r.get("role") in ("stock", "ai-generated"):
                auto_reject_failed_assets(project, [dict(r, role="stock", path=next(
                    (s.get("local_file") for s in project["shots"] if s["shot_id"] == r.get("shot_id")), ""))])
            n_rej += 1
    so = review.get("signoff")
    if so:
        final = Path(project.get("final_output", ""))
        final_json = out / f"final_report{_sfx(project)}.json"
        final_results = json.loads(final_json.read_text(encoding="utf-8")).get("results", []) if final_json.exists() else []
        final_open = resolve(project, final_results)[0] if final_results else FAIL
        if not final.exists() or so.get("video_sha") != file_hash(final):
            print("  ❌  Sign-off is for a different render than the current final video — review the new report")
        elif final_open != PASS:
            print("  ❌  Sign-off refused: the final safety check still has items that failed or await a decision")
        elif not so.get("name"):
            print("  ❌  Sign-off needs the reviewer's name")
        else:
            st["signoff"] = {"video_sha": so["video_sha"], "name": so["name"], "at": so.get("at") or _now(),
                             "confirmed": so.get("confirmed", []), "policy": POLICY_VERSION}
            _stamp_manifest(project, st["signoff"])
            print(f"  ✅  Human sign-off recorded for this render — {so['name']}")
    refresh_stage_status(project, proj_path)
    save_project(project, proj_path)
    print(f"  Review applied: {n_ok} approved · {n_rej} rejected")
    return 0


def refresh_stage_status(project: dict, proj_path: Path) -> None:
    """Recompute each stage's overall status after human decisions / overrides."""
    out, _, _ = _paths(project, proj_path)
    for stage in ("assets", "final"):
        f = out / f"{stage}_report{_sfx(project)}.json"
        if f.exists() and stage in project.get("safety_state", {}):
            results = json.loads(f.read_text(encoding="utf-8")).get("results", [])
            overall, fails, reviews = resolve(project, results)
            project["safety_state"][stage].update({"status": overall, "fails": len(fails), "reviews": len(reviews)})


def _stamp_manifest(project: dict, signoff: dict) -> None:
    mf = Path(project.get("render_manifest", ""))
    if mf.exists():
        m = json.loads(mf.read_text(encoding="utf-8"))
        m["content_safety"] = {"policy": POLICY_VERSION, "final": project.get("safety_state", {}).get("final", {}),
                               "signoff": signoff}
        mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")


def override(project: dict, proj_path: Path, key: str, reason: str) -> int:
    from svos_common import save_project
    if len((reason or "").strip()) < 15:
        print("  ❌  An override needs a written reason (15+ characters) — it is kept in the audit trail")
        return 1
    st = _state(project)
    st["overrides"][key] = {"reason": reason.strip(), "at": _now(), "by": os.environ.get("USERNAME") or os.environ.get("USER", "")}
    st["cleared"][key] = {"label": "override", "at": _now(), "by": "override"}
    refresh_stage_status(project, proj_path)
    save_project(project, proj_path)
    print(f"  ⚠️  Override recorded for {key}. It is listed in the safety report and the render manifest.")
    return 0


# ── Gate helpers used by the render stages ────────────────────────────────────
def is_cleared(project: dict, key: str) -> bool:
    st = project.get("safety_state", {})
    return key in st.get("cleared", {}) and key not in st.get("rejections", {})


def require_media(project: dict, path, illustration_only=False, window=None) -> str:
    """'' when the file has been cleared for this use, else a message explaining why it may not be rendered."""
    try:
        k = media_key(path, illustration_only, window)
    except FileNotFoundError:
        return f"file not found: {path}"
    return "" if is_cleared(project, k) else f"{Path(path).name} has not passed the content-safety check"


def require_text(project: dict, text: str) -> str:
    return "" if is_cleared(project, text_key(text)) else f"text “{text[:40]}” has not passed the content-safety check"


def require_audio(project: dict, path) -> str:
    try:
        k = audio_key(path)
    except FileNotFoundError:
        return f"file not found: {path}"
    return "" if is_cleared(project, k) else f"{Path(path).name} has not passed the content-safety check"


def screen_file(path: Path, illustration_only=False, window=None, mdir: Path | None = None, work: Path | None = None,
                ocr: bool = True, _eng: list = []) -> dict:
    """Quick screen used by the fetcher on each download / contact-sheet thumbnail (cached per process)."""
    if not _eng:
        _eng.append(Engines(mdir or models_dir()))
    work = work or Path(tempfile.mkdtemp(prefix="svos_safety_"))
    return judge_media(_eng[0], Path(path), work, illustration_only, window, fps=1.0, max_frames=8,
                       ocr_every=2 if ocr else 10 ** 6)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="U-rated content gate — local models only")
    ap.add_argument("project", nargs="?", help="Path to project.json")
    ap.add_argument("--setup", action="store_true", help="Download and verify the safety models")
    ap.add_argument("--stage", choices=("assets", "final"))
    ap.add_argument("--apply-review", metavar="SAFETY_REVIEW_JSON")
    ap.add_argument("--override", metavar="KEY")
    ap.add_argument("--reason", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check-text", metavar="TEXT")
    args = ap.parse_args()

    if args.check_text is not None:
        st, hits = check_text(args.check_text)
        print(st, *hits, sep="\n  ")
        sys.exit({PASS: 0, REVIEW: 3, FAIL: 1}[st])
    project = None
    if args.project:
        sys.path.insert(0, str(HERE))
        from svos_common import load_project
        project = load_project(Path(args.project))
    if args.setup:
        sys.exit(setup(models_dir(project)))
    if not project:
        ap.error("project.json is required")
    proj_path = Path(args.project)
    if args.apply_review:
        p = Path(args.apply_review)
        if not p.exists():
            sys.exit(f"❌  not found: {p}")
        try:
            review = json.loads(p.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            sys.exit(f"❌  {p.name} is not valid JSON ({e})")
        sys.exit(apply_review(project, proj_path, review))
    if args.override:
        sys.exit(override(project, proj_path, args.override, args.reason))
    if args.status:
        st = project.get("safety_state", {})
        final = Path(project.get("final_output", ""))
        cur = file_hash(final) if final.exists() else ""
        so = st.get("signoff", {})
        print(json.dumps({"assets": st.get("assets"), "final": st.get("final"),
                          "signed_off_for_current_render": bool(cur and so.get("video_sha") == cur),
                          "overrides": st.get("overrides", {})}, indent=2, ensure_ascii=False))
        sys.exit(0)
    if args.stage:
        sys.exit(run_stage(project, proj_path, args.stage))
    ap.print_help()


if __name__ == "__main__":
    main()
