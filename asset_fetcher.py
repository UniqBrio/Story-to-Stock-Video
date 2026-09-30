#!/usr/bin/env python3
"""
asset_fetcher.py  (SVOS v2 render layer)
────────────────
For each STOCK shot (T2/T3) in project.json:
  1. Runs each comma-separated keyword phrase as its own query across Pexels
     Videos → Pixabay Videos → Pexels/Pixabay/Unsplash images. If nothing
     clears the bar it walks a fallback ladder: shorter queries + the scene
     description, then the same queries without the orientation filter.
  2. Scores every candidate: post-crop resolution · aspect fit · duration fit ·
     the provider's own result position · how well the provider's tags/alt
     text match the shot · query order · photographer already used elsewhere.
     Clips shorter than the shot, assets used by another shot and assets
     rejected at review are never offered.
  3. Downloads the winner (or, with --candidates N, writes a CONTACT SHEET for
     G4 approval and downloads nothing until --apply-picks).
  4. Writes local_file / source / license / page_url / score back to project.json
     and logs to the Download Log + Asset Tracker sheets.

Request hygiene: a per-provider request budget per run, an on-disk response
cache (Assets/_cache/api), Retry-After aware backoff, the smallest rendition
that still fills the frame, and a size-capped, probe-validated download.

Review loop: shots with status swap/error are always re-fetched and the
rejected asset is remembered (rejected_ids) so it is never offered again.
validation_report.html saves review.json → --apply-review review.json.

Cards (T1/T5) and product inserts (T4) are never fetched.

Usage:
    python asset_fetcher.py project.json                      # auto-pick + download
    python asset_fetcher.py project.json --candidates 8       # contact_sheet.html for G4, no downloads
    python asset_fetcher.py project.json --apply-picks picks.json
    python asset_fetcher.py project.json --apply-review review.json
    python asset_fetcher.py project.json --refetch            # re-download even if file exists
    python asset_fetcher.py project.json --shot 003

API keys: env PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_API_KEY override the sheet.
Exit code 2 when any stock shot ends without an asset.
"""

import re
import sys
import json
import time
import html
import random
import hashlib
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime

from svos_common import (load_project, save_project, shot_kind, banner, env_key, set_log,
                         assets_dir, output_dir, infer_asset_type, probe_video_info)

try:
    import openpyxl
except ImportError:
    sys.exit("❌  Run: pip install openpyxl")


PEXELS_VIDEO_URL   = "https://api.pexels.com/videos/search"
PEXELS_IMAGE_URL   = "https://api.pexels.com/v1/search"
PIXABAY_VIDEO_URL  = "https://pixabay.com/api/videos/"
PIXABAY_IMAGE_URL  = "https://pixabay.com/api/"
UNSPLASH_IMAGE_URL = "https://api.unsplash.com/search/photos"

LICENSES = {
    "pexels":   "Pexels License — free for commercial use, no attribution required (https://www.pexels.com/license/)",
    "pixabay":  "Pixabay Content License — free for commercial use, no attribution required (https://pixabay.com/service/license-summary/)",
    "unsplash": "Unsplash License — free for commercial use, no attribution required (https://unsplash.com/license)",
}
PROVIDERS = ("pexels", "pixabay", "unsplash")

# Defaults; the Project Settings sheet overrides them (story_reader → project["fetch"]).
# Budgets sit under the providers' own limits: Pexels 200/h, Pixabay 100/min, Unsplash demo 50/h.
DEFAULT_FETCH = {
    "max_queries_per_shot": 3,
    "budget": {"pexels": 150, "pixabay": 300, "unsplash": 40},
    "cache_hours": 24.0,
    "max_download_mb": 300,
}
MAX_RETRIES   = 4
MAX_WAIT_S    = 60          # never sleep longer than this for one Retry-After
RATE_DELAY    = 0.35
IMAGE_LONG_PX = 3000        # request stills at this long edge instead of 20+ MP originals
PIXABAY_IMAGE_MAX = 1280    # largeImageURL is capped at 1280 px on the long edge
UA = "UniqBrio-SVOS/2.0"

STOPWORDS = set("""a an the and or of in on at to for with from by into over under near up down out off is are was
were be being been it its this that these those his her their our your my as while very more most some any no not
person people shot scene video clip stock footage image photo""".split())

# ── Run state (reset by configure()) ─────────────────────────────────────────
_key_ok: dict = {}
_used: dict = {}
_budget: dict = {}
_warned: set = set()
_cfg: dict = {}


def configure(fetch_cfg: dict | None, cache_dir: Path | None) -> None:
    cfg = {**DEFAULT_FETCH, **{k: v for k, v in (fetch_cfg or {}).items() if v not in (None, "")}}
    cfg["budget"] = {**DEFAULT_FETCH["budget"], **(fetch_cfg or {}).get("budget", {})}
    cfg["cache_dir"] = cache_dir
    _cfg.clear(); _cfg.update(cfg)
    _key_ok.clear(); _key_ok.update({p: True for p in PROVIDERS})
    _used.clear(); _used.update({p: 0 for p in PROVIDERS})
    _budget.clear(); _budget.update({p: int(cfg["budget"].get(p, 100)) for p in PROVIDERS})
    _warned.clear()


def _warn_once(tag: str, msg: str) -> None:
    if tag not in _warned:
        _warned.add(tag)
        print(msg)


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _http_json(url: str, headers: dict | None = None, timeout: int = 20):
    """GET JSON → (data, error, ratelimit_remaining). Retries 429/5xx with Retry-After or backoff."""
    hdrs = {"User-Agent": UA, **(headers or {})}
    last = "unknown error"
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")), None, r.headers.get("X-Ratelimit-Remaining")
        except urllib.error.HTTPError as e:
            if e.code != 429 and not 500 <= e.code < 600:
                return None, f"HTTP {e.code}", None
            last = "rate limited (HTTP 429)" if e.code == 429 else f"HTTP {e.code}"
            ra = (e.headers.get("Retry-After") if e.headers else None) or ""
            wait = float(ra) if ra.strip().isdigit() else min(30.0, 2.0 ** (attempt + 1)) + random.random()
            if wait > MAX_WAIT_S:
                return None, f"{last} — provider asks to wait {int(wait)}s; try again later", None
        except Exception as ex:                              # timeouts, DNS, TLS, bad JSON
            last = str(ex) or ex.__class__.__name__
            wait = min(30.0, 2.0 ** attempt) + random.random()
        if attempt < MAX_RETRIES - 1:
            print(f"    ⏳  {last} — retrying in {wait:.0f}s")
            time.sleep(wait)
    return None, f"request failed ({last})", None


def _cache_file(provider: str, url_no_secret: str) -> Path | None:
    d = _cfg.get("cache_dir")
    if not d:
        return None
    return Path(d) / provider / (hashlib.sha1(url_no_secret.encode("utf-8")).hexdigest() + ".json")


def _api_get(provider: str, url: str, headers: dict | None = None, secret: str = ""):
    """Provider GET with disk cache, per-run request budget and key-rejection latch → (data, error)."""
    cf = _cache_file(provider, url.replace(secret, "***") if secret else url)
    ttl = float(_cfg.get("cache_hours", 24)) * 3600
    if cf and cf.exists() and time.time() - cf.stat().st_mtime < ttl:
        try:
            return json.loads(cf.read_text(encoding="utf-8")), None
        except Exception:
            pass
    if not _key_ok.get(provider, True):
        return None, "key rejected"
    if _used[provider] >= _budget[provider]:
        _warn_once(f"budget:{provider}", f"    ⚠️  {provider}: request budget of {_budget[provider]} for this run is used up "
                                         f"— remaining searches skip {provider} (raise fetch_budget_{provider} if your plan allows)")
        return None, "budget exhausted"
    _used[provider] += 1
    data, err, remaining = _http_json(url, headers)
    time.sleep(RATE_DELAY)
    if err and any(code in err for code in ("HTTP 401", "HTTP 403")) or (provider == "pixabay" and err == "HTTP 400"):
        _key_ok[provider] = False
        env = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY", "unsplash": "UNSPLASH_API_KEY"}[provider]
        _warn_once(f"key:{provider}", f"    ❌  {provider} rejected the API key ({err}). Get a new key and set {env}.")
        return None, err
    if err:
        _warn_once(f"err:{provider}:{err}", f"    ⚠️  {provider}: {err}")
        return None, err
    try:
        if remaining is not None and int(remaining) < 20:
            _warn_once(f"low:{provider}", f"    ⚠️  {provider}: only {remaining} requests left in the provider's window")
    except ValueError:
        pass
    if cf and data is not None:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass
    return data, None


_EXT_BY_TYPE = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
                "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _download(url: str, dest: Path, max_mb: float = 300, timeout: int = 60) -> Path | None:
    """Stream to dest (extension corrected from Content-Type); size-capped and probe-validated."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    limit = int(max_mb * 1024 * 1024)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            length = int(r.headers.get("Content-Length") or 0)
            if length > limit:
                print(f"    ❌  Download is {length / 1048576:.0f} MB (limit {max_mb:.0f} MB) — skipped")
                return None
            got = 0
            with open(tmp, "wb") as f:
                while chunk := r.read(1 << 16):
                    got += len(chunk)
                    if got > limit:
                        raise IOError(f"exceeded {max_mb:.0f} MB")
                    f.write(chunk)
        if ctype in _EXT_BY_TYPE:
            dest = dest.with_suffix(_EXT_BY_TYPE[ctype])
        if tmp.stat().st_size < 10 * 1024:
            print(f"    ❌  Download too small ({tmp.stat().st_size} bytes) — discarding")
            tmp.unlink(missing_ok=True)
            return None
        tmp.replace(dest)
        if probe_video_info(dest)["width"] <= 0:           # HTML error page, truncated file, wrong type…
            print(f"    ❌  {dest.name} is not a readable image/video — discarding")
            dest.unlink(missing_ok=True)
            return None
        return dest
    except Exception as e:
        print(f"    ❌  Download failed: {e}")
        tmp.unlink(missing_ok=True)
        return None


# ── Scoring ───────────────────────────────────────────────────────────────────
def _aspect_score(w, h, tw, th) -> int:
    if not w or not h:
        return 0
    diff = abs(w / h - tw / th)
    if diff < 0.05:
        return 30
    if diff < 0.25:
        return 18
    # Orientation mismatch (landscape source for a portrait target): usable only with a heavy crop
    return 6 if (w > h) == (tw > th) else 0


def _res_score(w, h, tw, th) -> int:
    # What matters is the post-crop resolution: for a portrait target from a
    # landscape source, the usable width is h * tw/th.
    if not w or not h:
        return 0
    if (tw > th) == (w > h):
        usable_h = h
    else:
        usable_h = min(h, int(w * th / tw)) if tw < th else min(w, int(h * tw / th))
    if usable_h >= th * 1.0:
        return 30
    if usable_h >= th * 0.75:
        return 20
    if usable_h >= th * 0.5:
        return 8
    return 0


def _pick_file(files: list, tw: int, th: int) -> dict | None:
    """
    Video rendition: target orientation first, then the SMALLEST file that still
    fills the frame (a 4K master adds download time, not quality, at 1080×1920).
    """
    files = [f for f in files if f.get("width") and f.get("height")]
    if not files:
        return None
    portrait = th > tw
    same = [f for f in files if (f["height"] > f["width"]) == portrait] or files
    full = [f for f in same if _res_score(f["width"], f["height"], tw, th) >= 30]
    if full:
        return min(full, key=lambda f: f["width"] * f["height"])
    return max(same, key=lambda f: f["width"] * f["height"])


def _image_request(w: int, h: int, cap: int = IMAGE_LONG_PX) -> tuple[str, int, int]:
    """URL size parameter + resulting dimensions for a still capped at `cap` on the long edge."""
    if not w or not h or max(w, h) <= cap:
        return "", int(w or 0), int(h or 0)
    if w >= h:
        return f"w={cap}", cap, int(h * cap / w)
    return f"h={cap}", int(w * cap / h), cap


def _words(text: str) -> set:
    out = set()
    for t in re.findall(r"[a-z]+", (text or "").lower()):
        if len(t) < 3 or t in STOPWORDS:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                t = t[: -len(suf)]
                break
        out.add(t)
    return out


def relevance(c: dict, shot: dict) -> float:
    """Share of the shot's content words found in the provider's own description/tags (0–1)."""
    want = _words(" ".join(shot.get("keywords", [])) + " " + shot.get("scene_desc", ""))
    have = _words(c.get("text", ""))
    if not want or not have:
        return 0.0
    return len(want & have) / len(want)


def score_candidate(c: dict, shot_dur: float, tw: int, th: int, shot: dict | None = None) -> int:
    w, h = c.get("width", 0), c.get("height", 0)
    s = _res_score(w, h, tw, th) + _aspect_score(w, h, tw, th)
    if c["type"] == "video":
        d = float(c.get("duration") or 0)
        if d and d < shot_dur:
            return 0                        # disqualify: would need a freeze/loop
        if d >= shot_dur + 1.0:
            s += 20
        elif d >= shot_dur:
            s += 12
        s += 6                              # motion beats stills for Reels
    s += max(0, 6 - 2 * c.get("query_rank", 0))          # earlier phrase = more literal
    s += max(0, 10 - c.get("pos", 0))                     # the provider's own relevance order
    rel = relevance(c, shot or {})
    c["relevance"] = round(rel, 2)
    s += round(20 * rel)
    s += {"pexels": 4, "pixabay": 3, "unsplash": 2}.get(c["source"], 0)
    s -= 5 * c.get("ladder", 0)                           # found only after relaxing the search
    return max(1, int(s))


def asset_key(source, ctype, cid) -> str:
    return f"{source}:{ctype}:{cid}"


# ── Source searchers (all return normalised candidate dicts) ──────────────────
def _cand(source, ctype, cid, url, w, h, dur, thumb, page, author, query, rank, pos, text="", **extra):
    return {"source": source, "type": ctype, "id": str(cid), "url": url, "width": int(w or 0),
            "height": int(h or 0), "duration": float(dur or 0), "thumb": thumb or "",
            "page_url": page or "", "author": author or "", "query": query, "query_rank": rank,
            "pos": pos, "text": text or "", "license": LICENSES.get(source, ""), **extra}


def _slug_text(page_url: str) -> str:
    """Pexels page URLs carry the title: /video/woman-working-at-a-desk-123/ → 'woman working at a desk'."""
    m = re.search(r"/(?:video|photo)/([^/]+?)-?\d*/?$", page_url or "")
    return m.group(1).replace("-", " ") if m else ""


def search_pexels_videos(query, key, per_page, tw, th, rank, orient=True, illu=False):
    if not key or illu:
        return []
    url = f"{PEXELS_VIDEO_URL}?query={urllib.parse.quote(query)}&per_page={per_page}"
    if orient:
        url += f"&orientation={'portrait' if th > tw else 'landscape'}"
    data, _ = _api_get("pexels", url, {"Authorization": key})
    out = []
    for pos, v in enumerate((data or {}).get("videos", [])):
        f = _pick_file(v.get("video_files", []), tw, th)
        if not f:
            continue
        out.append(_cand("pexels", "video", v.get("id"), f.get("link", ""), f.get("width"), f.get("height"),
                         v.get("duration"), v.get("image", ""), v.get("url", ""),
                         (v.get("user") or {}).get("name", ""), query, rank, pos, _slug_text(v.get("url", ""))))
    return out


def search_pixabay_videos(query, key, per_page, tw, th, rank, orient=True, illu=False):
    if not key:
        return []
    url = (f"{PIXABAY_VIDEO_URL}?key={key}&q={urllib.parse.quote(query)}"
           f"&per_page={max(3, per_page)}&video_type={'animation' if illu else 'film'}&safesearch=true")
    data, _ = _api_get("pixabay", url, secret=key)
    out = []
    for pos, hit in enumerate((data or {}).get("hits", [])):
        files, thumb = [], ""
        for vd in (hit.get("videos") or {}).values():
            if isinstance(vd, dict) and vd.get("url"):
                files.append({"link": vd["url"], "width": vd.get("width", 0), "height": vd.get("height", 0)})
                thumb = thumb or vd.get("thumbnail", "")
        f = _pick_file(files, tw, th)
        if not f:
            continue
        out.append(_cand("pixabay", "video", hit.get("id"), f["link"], f["width"], f["height"],
                         hit.get("duration"), thumb, hit.get("pageURL", ""), hit.get("user", ""), query, rank, pos,
                         hit.get("tags", "")))
    return out


def search_pexels_images(query, key, per_page, tw, th, rank, orient=True, illu=False):
    if not key or illu:
        return []
    url = f"{PEXELS_IMAGE_URL}?query={urllib.parse.quote(query)}&per_page={per_page}"
    if orient:
        url += f"&orientation={'portrait' if th > tw else 'landscape'}"
    data, _ = _api_get("pexels", url, {"Authorization": key})
    out = []
    for pos, p in enumerate((data or {}).get("photos", [])):
        src = p.get("src", {})
        orig = src.get("original") or src.get("large2x", "")
        size, w, h = _image_request(p.get("width"), p.get("height"))
        link = f"{orig}{'&' if '?' in orig else '?'}auto=compress&cs=tinysrgb&{size}" if (orig and size) else orig
        out.append(_cand("pexels", "image", p.get("id"), link, w, h, 0, src.get("medium", ""), p.get("url", ""),
                         p.get("photographer", ""), query, rank, pos,
                         f"{p.get('alt', '')} {_slug_text(p.get('url', ''))}"))
    return out


def search_pixabay_images(query, key, per_page, tw, th, rank, orient=True, illu=False):
    if not key:
        return []
    url = (f"{PIXABAY_IMAGE_URL}?key={key}&q={urllib.parse.quote(query)}"
           f"&per_page={max(3, per_page)}&image_type={'illustration' if illu else 'photo'}&safesearch=true")
    if orient:
        url += f"&orientation={'vertical' if th > tw else 'horizontal'}"
    data, _ = _api_get("pixabay", url, secret=key)
    out = []
    for pos, hit in enumerate((data or {}).get("hits", [])):
        _, w, h = _image_request(hit.get("imageWidth"), hit.get("imageHeight"), PIXABAY_IMAGE_MAX)
        out.append(_cand("pixabay", "image", hit.get("id"), hit.get("largeImageURL", ""), w, h, 0,
                         hit.get("previewURL", ""), hit.get("pageURL", ""), hit.get("user", ""), query, rank, pos,
                         hit.get("tags", "")))
    return out


def search_unsplash_images(query, key, per_page, tw, th, rank, orient=True, illu=False):
    if not key or illu:
        return []
    url = f"{UNSPLASH_IMAGE_URL}?query={urllib.parse.quote(query)}&per_page={per_page}&content_filter=high"
    if orient:
        url += f"&orientation={'portrait' if th > tw else 'landscape'}"
    data, _ = _api_get("unsplash", url, {"Authorization": f"Client-ID {key}"})
    out = []
    for pos, r in enumerate((data or {}).get("results", [])):
        urls, links = r.get("urls", {}), (r.get("links") or {})
        size, w, h = _image_request(r.get("width"), r.get("height"))
        link = f"{urls['raw']}&fm=jpg&q=85{'&' + size if size else ''}" if urls.get("raw") else urls.get("full", "")
        out.append(_cand("unsplash", "image", r.get("id"), link, w, h, 0, urls.get("small", ""),
                         links.get("html", ""), (r.get("user") or {}).get("name", ""), query, rank, pos,
                         f"{r.get('alt_description') or ''} {r.get('description') or ''}",
                         download_location=links.get("download_location", "")))
    return out


# ── Candidate gathering per shot ──────────────────────────────────────────────
def _relaxed(queries: list[str], scene: str) -> list[str]:
    """Ladder rung 1: shorter versions of each phrase + the scene description itself."""
    out = []
    for q in queries:
        ws = q.split()
        for n in (3, 2):
            if len(ws) > n:
                out.append(" ".join(ws[:n]))
    scene_words = [w for w in re.findall(r"[A-Za-z]+", scene or "") if len(w) > 2 and w.lower() not in STOPWORDS]
    if scene_words:
        out.append(" ".join(scene_words[:5]))
    seen, uniq = set(q.lower() for q in queries), []
    for q in out:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq


def _search(shot, queries, keys, tw, th, per_query, exclude, used_authors, ladder, orient) -> list[dict]:
    priority = shot.get("asset_priority", "video_first")
    want_video = priority in ("video_first", "video_only")
    want_image = priority in ("image_first", "image_only", "video_first")
    shot_dur = float(shot.get("duration", 4.0))
    illu = bool(shot.get("illustration_only"))           # swimwear-type subject: cartoons / animation only
    seen, cands = set(), []

    def add(lst):
        for c in lst:
            k = asset_key(c["source"], c["type"], c["id"])
            if k in seen or k in exclude or not c["url"]:
                continue
            seen.add(k)
            c["ladder"] = ladder
            c["score"] = score_candidate(c, shot_dur, tw, th, shot)
            if c["type"] == "video" and c["duration"] and c["duration"] < shot_dur:
                continue
            if c["author"] and (c["source"], c["author"]) in used_authors:
                c["score"] -= 8                              # same photographer ≈ same shoot as another shot
                c["same_author"] = True
            cands.append(c)

    for rank, q in enumerate(queries):
        if want_video:
            add(search_pexels_videos(q, keys.get("pexels", ""), per_query, tw, th, rank, orient, illu))
            add(search_pixabay_videos(q, keys.get("pixabay", ""), per_query, tw, th, rank, orient, illu))
    have_good_video = any(c["type"] == "video" and c["score"] >= 60 for c in cands)
    if want_image and (priority in ("image_first", "image_only") or not have_good_video):
        for rank, q in enumerate(queries):
            add(search_pexels_images(q, keys.get("pexels", ""), per_query, tw, th, rank, orient, illu))
            add(search_pixabay_images(q, keys.get("pixabay", ""), per_query, tw, th, rank, orient, illu))
            add(search_unsplash_images(q, keys.get("unsplash", ""), per_query, tw, th, rank, orient, illu))

    if priority == "video_only":
        cands = [c for c in cands if c["type"] == "video"]
    if priority == "image_only":
        cands = [c for c in cands if c["type"] == "image"]
    cands.sort(key=lambda c: (-c["score"], c["query_rank"], c["pos"]))
    return cands


def gather_candidates(shot: dict, keys: dict, tw: int, th: int, per_query: int,
                      exclude: set, used_authors: set | None = None) -> list[dict]:
    max_q = int(_cfg.get("max_queries_per_shot", 3) or 3)
    phrases = [q for q in shot.get("keywords", []) if q.strip()]
    if len(phrases) > max_q:
        print(f"    ℹ️  {shot['shot_id']} — using the first {max_q} of {len(phrases)} keyword phrases "
              f"(max_queries_per_shot)")
    queries = phrases[:max_q] or ([shot["scene_desc"][:80]] if shot.get("scene_desc") else [])
    rungs = [(0, queries, True),
             (1, _relaxed(queries, shot.get("scene_desc", ""))[:max_q + 1], True),
             (2, queries, False)]
    for ladder, qs, orient in rungs:
        if not qs:
            continue
        cands = _search(shot, qs, keys, tw, th, per_query, exclude, used_authors or set(), ladder, orient)
        if cands:
            if ladder:
                print(f"    ↘   {shot['shot_id']} — nothing on the first search; found "
                      f"{len(cands)} after {'shortening the queries' if ladder == 1 else 'dropping the orientation filter'}")
            return cands
    return []


def slug_for(shot: dict) -> str:
    base = shot["keywords"][0] if shot.get("keywords") else shot.get("scene_desc", "scene")
    s = "_".join(base.split()[:3]).lower()
    return "".join(ch if ch.isalnum() or ch == "_" else "" for ch in s) or "scene"


ASSET_FIELDS = ("asset_type", "source", "local_file", "asset_id", "asset_url", "page_url", "author", "license",
                "asset_score", "asset_resolution", "asset_duration", "query_used", "approved")


def reject_current(shot: dict, status: str = "swap") -> bool:
    """Mark the shot's current asset as rejected so it is never offered again. Returns True if one existed."""
    had = bool(shot.get("asset_id"))
    if had:
        rid = asset_key(shot.get("source"), shot.get("asset_type"), shot.get("asset_id"))
        shot.setdefault("rejected_ids", [])
        if rid not in shot["rejected_ids"]:
            shot["rejected_ids"].append(rid)
    for f in ASSET_FIELDS:
        shot.pop(f, None)
    shot["local_file"] = ""
    shot["status"] = status
    return had


# ── Content safety (U-rated) ─────────────────────────────────────────────────
def safety_screen(shot: dict, path: Path, ocr: bool = True) -> tuple[str, list]:
    """Local-model screen of one download or thumbnail → (PASS | REVIEW | FAIL, reasons)."""
    import content_safety as safety
    window = (float(shot.get("trim_in", 0) or 0), float(shot.get("duration", 4.0))) \
        if infer_asset_type(str(path)) == "video" else None
    r = safety.screen_file(Path(path), bool(shot.get("illustration_only")), window, _cfg.get("models_dir"), ocr=ocr)
    return r["status"], r["reasons"]


def _remember_rejection(shot: dict, c: dict, reasons: list) -> None:
    k = asset_key(c["source"], c["type"], c["id"])
    shot.setdefault("rejected_ids", [])
    if k not in shot["rejected_ids"]:
        shot["rejected_ids"].append(k)
    shot.setdefault("safety_rejections", []).append({"asset": k, "reasons": reasons[:4], "at": datetime.now().isoformat(timespec="seconds")})


def fetch_and_screen(shot: dict, c: dict, afolder: Path, allow_review: bool) -> str:
    """Download → screen. Returns ok | review | fail | download (a REVIEW asset is kept only if allow_review)."""
    if not download_candidate(shot, c, afolder):
        return "download"
    path = Path(shot["local_file"])
    status, reasons = safety_screen(shot, path)
    if status == "PASS" or (status == "REVIEW" and allow_review):
        shot["safety_screen"] = {"status": status, "reasons": reasons[:4]}
        return "ok" if status == "PASS" else "review"
    print(f"    🛡   {shot['shot_id']} — {c['source']}#{c['id']} {status}: {'; '.join(reasons[:2])} — discarded, never offered again")
    path.unlink(missing_ok=True)
    reject_current(shot, "error")
    _remember_rejection(shot, c, reasons)
    return "fail"


def download_candidate(shot: dict, c: dict, afolder: Path) -> bool:
    sub = "Videos" if c["type"] == "video" else "Images"
    ext = ".mp4" if c["type"] == "video" else ".jpg"
    dest = afolder / sub / f"shot_{shot['shot_id']}_{slug_for(shot)}{ext}"
    print(f"    ↓   {shot['shot_id']} [{c['type']}] {c['source']}#{c['id']} score={c['score']} "
          f"rel={c.get('relevance', 0):.2f} {c['width']}×{c['height']}"
          f"{(' ' + str(round(c['duration'], 1)) + 's') if c['duration'] else ''} → {dest.name}")
    got = _download(c["url"], dest, float(_cfg.get("max_download_mb", 300)))
    if not got:
        return False
    if c.get("download_location") and c["source"] == "unsplash":
        # Unsplash API terms: every download must be reported to download_location
        key = env_key("UNSPLASH_API_KEY", _cfg.get("unsplash_key", ""))
        _http_json(c["download_location"], {"Authorization": f"Client-ID {key}"} if key else None)
    shot.update({
        "asset_type": c["type"], "source": c["source"], "local_file": str(got), "status": "downloaded",
        "asset_id": c["id"], "asset_url": c["url"], "page_url": c["page_url"], "author": c["author"],
        "license": c["license"], "asset_score": c["score"],
        "asset_resolution": f"{c['width']}x{c['height']}", "asset_duration": c["duration"],
        "query_used": c["query"],
    })
    shot.pop("approved", None)
    return True


def screen_thumbnails(shot: dict, cands: list, afolder: Path, want: int) -> list:
    """Contact sheet: screen each candidate's preview; FAIL is hidden, REVIEW is badged."""
    out, cache, hidden = [], afolder / "_cache" / "thumbs", 0
    cache.mkdir(parents=True, exist_ok=True)
    for c in cands:
        if len(out) >= want:
            break
        if not c.get("thumb"):
            c["safety"] = "unchecked"
            out.append(c)
            continue
        tp = cache / f"{c['source']}_{c['type']}_{c['id']}.jpg"
        try:
            if not tp.exists():
                with urllib.request.urlopen(urllib.request.Request(c["thumb"], headers={"User-Agent": UA}), timeout=20) as r:
                    tp.write_bytes(r.read())
            status, reasons = safety_screen(shot, tp, ocr=False)
        except Exception as e:
            status, reasons = "REVIEW", [f"preview could not be checked ({e})"]
        if status == "FAIL":
            _remember_rejection(shot, c, reasons)
            hidden += 1
            continue
        c["safety"] = status.lower()
        c["safety_reasons"] = reasons[:3]
        out.append(c)
    if hidden:
        print(f"    🛡   {shot['shot_id']} — hid {hidden} candidate(s) that failed the U-rated check")
    return out


# ── Contact sheet (G4) ────────────────────────────────────────────────────────
def write_contact_sheet(project: dict, per_shot: dict, out_path: Path, xlsx_name: str = "story_plan.xlsx"):
    name = html.escape(project.get("project_name", "My Video"))
    cards = ""
    for shot in project["shots"]:
        sid = shot["shot_id"]
        cands = per_shot.get(sid)
        if cands is None:
            continue
        rows = ""
        for i, c in enumerate(cands):
            thumb = f'<img src="{html.escape(c["thumb"])}" loading="lazy">' if c["thumb"] else '<div class="nothumb">no preview</div>'
            dur = f"{c['duration']:.1f}s" if c["duration"] else "still"
            also = (f'<span class="warn">also offered for shot {", ".join(html.escape(x) for x in c["also_in"])}</span>'
                    if c.get("also_in") else "")
            if c.get("safety") == "review":
                also += (f'<span class="warn">🛡 needs a safety review: '
                         f'{html.escape("; ".join(c.get("safety_reasons", [])))}</span>')
            rows += f"""
            <label class="cand">
              <input type="radio" name="pick_{html.escape(sid)}" value="{i}" {'checked' if i == 0 else ''}
                     onchange="update()">
              {thumb}
              <div class="meta">
                <b>#{i+1} · {html.escape(c['source'])} · {dur}</b>
                <span>{c['width']}×{c['height']} · score {c['score']} · match {int(100 * c.get('relevance', 0))}%</span>
                <span class="q">“{html.escape(c['query'])}”</span>
                {also}
                <a href="{html.escape(c['page_url'])}" target="_blank">source page ↗</a>
              </div>
            </label>"""
        if not cands:
            rows = '<p class="empty">No candidates cleared the bar — widen the keywords (Lane 2/3) or rewrite the shot (Card 3).</p>'
        cards += f"""
        <section class="shot" id="shot_{html.escape(sid)}">
          <header><span class="sid">SHOT {html.escape(sid)}</span>
            <span class="desc">{html.escape(shot.get('scene_desc',''))}</span>
            <span class="dur">⏱ {shot.get('duration')}s · {html.escape(shot.get('treatment','T2'))}</span></header>
          <div class="kw">{' · '.join(html.escape(k) for k in shot.get('keywords', []))}{' · <b style="color:#e67e22">cartoon / illustration only</b>' if shot.get('illustration_only') else ''}</div>
          <div class="grid">{rows}</div>
          <label class="skip"><input type="checkbox" name="skip_{html.escape(sid)}" onchange="update()"> reject all — send shot back to storyboard</label>
        </section>"""

    doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contact Sheet — {name}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,sans-serif;background:#0f0f17;color:#e8e4dc;margin:0;padding:22px;line-height:1.5}}
 h1{{color:#DE7D14;font-size:22px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:20px}}
 .shot{{background:#1a1a2e;border:1px solid #2a2a40;border-radius:12px;padding:18px 20px;margin-bottom:18px}}
 header{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:6px}}
 .sid{{background:#DE7D14;color:#fff;font-weight:700;padding:3px 10px;border-radius:6px;font-size:12px}}
 .desc{{color:#c5b8f0;font-weight:600}} .dur{{color:#888;font-size:12px;margin-left:auto}}
 .kw{{font-size:12px;color:#777;margin-bottom:12px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}}
 .cand{{background:#111120;border:2px solid #2a2a40;border-radius:10px;padding:8px;cursor:pointer;display:block}}
 .cand:has(input:checked){{border-color:#27ae60}} .cand input{{float:right}}
 .cand img,.nothumb{{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:6px;background:#000;display:block}}
 .nothumb{{display:flex;align-items:center;justify-content:center;color:#555;font-size:12px}}
 .meta{{font-size:12px;color:#aaa;display:flex;flex-direction:column;gap:2px;margin-top:6px}} .meta b{{color:#e8e4dc}}
 .q{{color:#6d5fa0;font-style:italic}} .meta a{{color:#DE7D14;text-decoration:none}} .warn{{color:#e67e22}}
 .skip{{display:block;margin-top:10px;font-size:12px;color:#c0392b}} .empty{{color:#c0392b}}
 #out{{position:sticky;bottom:0;background:#0a1a0a;border:1px solid #27ae60;border-radius:10px;padding:14px 18px;margin-top:20px}}
 #dupes{{color:#e67e22;font-size:13px;margin:6px 0}}
 textarea{{width:100%;min-height:90px;background:#0f0f17;color:#c5f0c5;border:1px solid #333;border-radius:6px;font-family:monospace;font-size:12px}}
 button{{background:#DE7D14;color:#fff;border:none;padding:8px 16px;border-radius:6px;font-weight:600;cursor:pointer;margin-right:6px}}
</style></head><body>
<h1>🎞 Contact Sheet — {name}</h1>
<div class="sub">G4 approval · generated {datetime.now():%d %b %Y %H:%M} · pick one candidate per shot, click
 <b>Save picks.json</b>, then run <code>python run_pipeline.py {html.escape(xlsx_name)} --apply-picks &lt;path to picks.json&gt;</code></div>
{cards}
<div id="out"><b style="color:#27ae60">picks.json</b>
<button onclick="savePicks()">💾 Save picks.json</button><button onclick="copyPicks()">📋 Copy</button>
<div id="dupes"></div>
<textarea id="picks" readonly></textarea></div>
<script>
const CANDS={json.dumps({sid: [c["source"] + ":" + c["type"] + ":" + c["id"] for c in cs] for sid, cs in per_shot.items()})};
function update(){{
  const picks={{}}, taken={{}}, dupes=[];
  document.querySelectorAll('section.shot').forEach(s=>{{
    const sid=s.id.replace('shot_','');
    const skip=s.querySelector('input[name="skip_'+sid+'"]');
    if(skip&&skip.checked){{picks[sid]='reject';return;}}
    const r=s.querySelector('input[name="pick_'+sid+'"]:checked');
    if(r){{picks[sid]=parseInt(r.value);
      const k=(CANDS[sid]||[])[picks[sid]];
      if(k&&taken[k])dupes.push('shots '+taken[k]+' and '+sid+' picked the same asset'); else if(k)taken[k]=sid;}}
  }});
  document.getElementById('picks').value=JSON.stringify(picks,null,2);
  document.getElementById('dupes').textContent=dupes.length?'⚠️ '+dupes.join(' · ')+' — pick a different one':'';
}}
function savePicks(){{update();
  const b=new Blob([document.getElementById('picks').value],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='picks.json';
  document.body.appendChild(a);a.click();a.remove();}}
function copyPicks(){{update();const t=document.getElementById('picks');
  if(navigator.clipboard){{navigator.clipboard.writeText(t.value);}}else{{t.select();document.execCommand('copy');}}}}
update();
</script></body></html>"""
    out_path.write_text(doc, encoding="utf-8")


# ── Excel logging ─────────────────────────────────────────────────────────────
def write_excel_logs(xlsx_path: Path, log_rows: list, tracker_rows: list):
    try:
        wb = openpyxl.load_workbook(xlsx_path)
        missing = [n for n in ("Download Log", "Asset Tracker") if n not in wb.sheetnames]
        if missing:
            print(f"  ℹ️  {xlsx_path.name} has no {' / '.join(missing)} sheet — those logs are skipped")
        if "Download Log" in wb.sheetnames:
            ws = wb["Download Log"]
            for row in log_rows:
                ws.append(row)
        if "Asset Tracker" in wb.sheetnames and tracker_rows:
            ws = wb["Asset Tracker"]
            existing = {str(r[0]) for r in ws.iter_rows(min_row=3, values_only=True) if r and r[0]}
            for row in tracker_rows:
                if str(row[0]) in existing:
                    for r in ws.iter_rows(min_row=3):
                        if str(r[0].value) == str(row[0]):
                            for ci, v in enumerate(row):
                                r[ci].value = v
                            break
                else:
                    ws.append(row)
        wb.save(xlsx_path)
        return True
    except Exception as e:
        print(f"  ⚠️  Could not update Excel logs: {e}\n      (Close story_plan.xlsx if it is open in Excel, then retry.)")
        return False


# ── Entry point ───────────────────────────────────────────────────────────────
def _load_json(path: str, what: str):
    p = Path(path)
    if not p.exists():
        sys.exit(f"❌  {what} not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"❌  {what} is not valid JSON ({e}). Save it again from the HTML page.")


REVIEW_REJECT = {"swap", "error", "reject", "no_file"}


def apply_review(project: dict, review: dict) -> tuple[int, int]:
    """review.json from validation_report.html: {shot_id: keep | swap | error}. Returns (kept, rejected)."""
    if not isinstance(review, dict):
        sys.exit("❌  review.json must be an object like {\"003\": \"swap\", \"004\": \"keep\"}")
    by_id = {s["shot_id"]: s for s in project["shots"]}
    kept = rejected = 0
    for sid, verdict in review.items():
        shot = by_id.get(str(sid)) or by_id.get(str(sid).zfill(3))
        v = str(verdict).strip().lower()
        if not shot:
            print(f"    ⚠️  review.json names shot {sid}, which is not in the plan — ignored")
        elif v in REVIEW_REJECT:
            had = reject_current(shot)
            rejected += 1
            print(f"    🔄  {shot['shot_id']} — marked {v} at review" + (" (that asset will not be offered again)" if had else ""))
        elif v in ("keep", "ok", "approved"):
            if shot.get("local_file") and Path(shot["local_file"]).exists():
                shot["approved"] = True
                if shot.get("status") in ("swap", "error"):
                    shot["status"] = "downloaded"
                kept += 1
        else:
            print(f"    ⚠️  shot {sid}: unknown review verdict '{verdict}' (use keep / swap / error) — ignored")
    return kept, rejected


def main():
    ap = argparse.ArgumentParser(description="Fetch stock assets for every stock shot in project.json")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--force", action="store_true",
                    help="No-op kept for compatibility — swap/error shots are always re-fetched")
    ap.add_argument("--refetch", action="store_true", help="Re-download every stock shot, even approved ones")
    ap.add_argument("--shot", default=None, help="Only this shot ID")
    ap.add_argument("--candidates", type=int, default=0, metavar="N",
                    help="Contact-sheet mode: gather up to N candidates per shot, write contact_sheet.html + candidates.json, download nothing")
    ap.add_argument("--apply-picks", default=None, metavar="PICKS_JSON",
                    help="Download the candidates chosen in the contact sheet")
    ap.add_argument("--apply-review", default=None, metavar="REVIEW_JSON",
                    help="Apply keep/swap verdicts saved from validation_report.html, then re-fetch swapped shots")
    args = ap.parse_args()

    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    keys = {
        "pexels":   env_key("PEXELS_API_KEY",   project.get("api_keys", {}).get("pexels", "")),
        "pixabay":  env_key("PIXABAY_API_KEY",  project.get("api_keys", {}).get("pixabay", "")),
        "unsplash": env_key("UNSPLASH_API_KEY", project.get("api_keys", {}).get("unsplash", "")),
    }
    keys = {k: ("" if "YOUR_" in v else v) for k, v in keys.items()}
    tw, th = project.get("width", 1080), project.get("height", 1920)
    afolder = assets_dir(project, proj_path)
    configure(project.get("fetch"), afolder / "_cache" / "api")
    import content_safety as safety
    _cfg["models_dir"] = safety.models_dir(project)
    needs_fetch = any(shot_kind(s) == "stock" for s in project["shots"])
    if needs_fetch:
        blockers = safety.readiness(project)
        if blockers:
            for b in blockers:
                print(f"❌  {b}")
            sys.exit("❌  Content safety is mandatory: every download is screened before it can be used.")
    _cfg["unsplash_key"] = keys["unsplash"]

    if args.shot and not any(s["shot_id"] == args.shot for s in project["shots"]):
        sys.exit(f"❌  Shot '{args.shot}' not found")

    picks, per_shot_candidates = None, {}
    if args.apply_picks:
        cand_file = proj_path.parent / "candidates.json"
        if not cand_file.exists():
            sys.exit("❌  candidates.json not found next to project.json — run the contact sheet (--candidates N) first")
        per_shot_candidates = _load_json(str(cand_file), "candidates.json")
        picks = _load_json(args.apply_picks, "picks file")
        if not isinstance(picks, dict):
            sys.exit("❌  picks.json must be an object like {\"001\": 0, \"002\": \"reject\"}")
    if args.apply_review:
        kept, rejected = apply_review(project, _load_json(args.apply_review, "review file"))
        print(f"  Review applied: {kept} kept · {rejected} sent back for a new asset\n")

    shots = project["shots"] if not args.shot else [s for s in project["shots"] if s["shot_id"] == args.shot]
    banner("Asset Fetcher", f"{len(shots)} shots  |  assets → {afolder}",
           "mode: " + ("CONTACT SHEET (no downloads)" if args.candidates else
                       "APPLY PICKS" if args.apply_picks else "auto-pick + download"),
           "budget/run: " + " · ".join(f"{p} {_budget[p]}" for p in PROVIDERS)
           + f"  |  cache {_cfg.get('cache_hours')}h")
    missing = [k for k, v in keys.items() if not v]
    if missing:
        print(f"  ⚠️  No API key for: {missing}  (env PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_API_KEY)\n")

    def taken_by_others(shot) -> tuple[set, set]:
        ids, authors = set(), set()
        for s in project["shots"]:
            if s is shot or not s.get("asset_id") or s.get("status") != "downloaded":
                continue
            ids.add(asset_key(s.get("source"), s.get("asset_type"), s.get("asset_id")))
            if s.get("author"):
                authors.add((s.get("source"), s.get("author")))
        return ids, authors

    log_rows, tracker_rows = [], []
    ok = skipped = errors = 0
    picked_by: dict = {}

    for shot in shots:
        sid = shot["shot_id"]
        kind = shot_kind(shot)
        if kind in ("card", "logo_card"):
            print(f"    ▢   {sid} — {kind}: rendered as a card, no stock needed")
            shot["status"] = shot.get("status") if shot.get("status") == "downloaded" else "card"
            continue
        if kind == "product":
            exists = bool(shot.get("local_file") and Path(shot["local_file"]).exists())
            print(f"    🖥   {sid} — product insert from Demo Module Library: {'ok' if exists else 'FILE MISSING'}")
            if not exists:
                errors += 1
                shot["status"] = "error"
            else:
                shot["asset_type"] = shot.get("asset_type") or infer_asset_type(shot["local_file"]) or "video"
                shot["status"] = "downloaded"
            continue

        local = shot.get("local_file", "")
        status = shot.get("status", "")
        if status in ("swap", "error") and local and not args.candidates and not args.apply_picks:
            reject_current(shot, status)                 # the sheet / review said this asset is wrong
            local = ""
        if local and Path(local).exists() and not args.refetch and not args.candidates and not args.apply_picks:
            if not shot.get("asset_type"):
                shot["asset_type"] = infer_asset_type(local)
            if not shot.get("source"):
                shot.setdefault("license", "")
            print(f"    ⏭️  {sid} — file already exists, keeping {'approved ' if shot.get('approved') else ''}asset")
            skipped += 1
            continue

        exclude, used_authors = taken_by_others(shot)
        exclude |= set(shot.get("rejected_ids", []))

        if args.apply_picks:
            choice = picks.get(sid)
            cands = per_shot_candidates.get(sid, [])
            if choice in (None, "reject") or not cands:
                print(f"    ↩   {sid} — {'rejected at G4' if choice == 'reject' else 'no pick'}; back to storyboard")
                shot["status"] = "swap"
                errors += 1
                continue
            try:
                c = cands[int(choice)]
            except (ValueError, TypeError, IndexError):
                print(f"    ❌  {sid} — pick '{choice}' does not match the {len(cands)} candidates in candidates.json "
                      f"(was the contact sheet regenerated?) — pick again")
                shot["status"] = "swap"
                errors += 1
                continue
            k = asset_key(c["source"], c["type"], c["id"])
            if k in picked_by or k in exclude:
                other = picked_by.get(k) or "another shot / an earlier rejection"
                print(f"    ❌  {sid} — picked the same asset as {other}; each shot needs its own — pick again")
                shot["status"] = "swap"
                errors += 1
                continue
            res = fetch_and_screen(shot, c, afolder, allow_review=True)
            if res in ("ok", "review"):
                picked_by[k] = sid
                shot["approved"] = True
                ok += 1
                if res == "review":
                    print(f"    ⚠️  {sid} — kept, but the content-safety gate will ask you to approve it before rendering")
            else:
                shot["status"] = "swap" if res == "fail" else "error"; errors += 1
        else:
            per_query = max(3, args.candidates) if args.candidates else 5
            cands = gather_candidates(shot, keys, tw, th, per_query, exclude, used_authors)
            if args.candidates:
                cands = screen_thumbnails(shot, cands, afolder, args.candidates)
                per_shot_candidates[sid] = cands[:args.candidates]
                print(f"    🔎  {sid} — {len(cands)} candidates ({sum(1 for c in cands if c['type']=='video')} video)")
                continue
            if not cands:
                print(f"    ❌  {sid} — no suitable asset for {shot.get('keywords', [])[:3]}")
                shot["status"] = "error"; errors += 1
                continue
            downloaded, screened_out = False, 0
            for c in cands[:6]:                       # next best on a dead link or a content-safety reject
                res = fetch_and_screen(shot, c, afolder, allow_review=False)
                if res == "ok":
                    downloaded = True
                    break
                screened_out += res == "fail"
            if downloaded:
                ok += 1
            else:
                shot["status"] = "error"; errors += 1
                if screened_out:
                    print(f"    ❌  {sid} — {screened_out} candidate(s) failed the U-rated check; rewrite the keywords"
                          + (" (cartoon-only shot: try 'cartoon', 'illustration', 'animated')" if shot.get("illustration_only") else ""))

        log_rows.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid, shot.get("scene_desc", "")[:50],
                         shot.get("source", ""), shot.get("asset_url", ""), shot.get("local_file", ""),
                         shot.get("asset_resolution", ""), shot.get("status", "")])
        if shot.get("status") == "downloaded":
            tracker_rows.append([sid, shot.get("asset_type", ""), shot.get("source", ""),
                                 shot.get("asset_resolution", ""), shot.get("asset_duration", ""),
                                 shot.get("license", ""), shot.get("local_file", ""), "yes",
                                 shot.get("asset_score", ""), "", f"by {shot.get('author','')} · {shot.get('page_url','')}"])

    used = " · ".join(f"{p} {_used[p]}/{_budget[p]}" for p in PROVIDERS)
    if args.candidates:
        cand_path = proj_path.parent / "candidates.json"
        if args.shot and cand_path.exists():               # a single-shot rerun must not wipe the other shots
            try:
                merged = json.loads(cand_path.read_text(encoding="utf-8-sig"))
                merged.update(per_shot_candidates)
                per_shot_candidates = merged
            except Exception:
                pass
        offered: dict = {}
        for s_id, cs in per_shot_candidates.items():
            for c in cs:
                offered.setdefault(asset_key(c["source"], c["type"], c["id"]), []).append(s_id)
        for s_id, cs in per_shot_candidates.items():
            for c in cs:
                c["also_in"] = [x for x in offered[asset_key(c["source"], c["type"], c["id"])] if x != s_id]
        cand_path.write_text(json.dumps(per_shot_candidates, indent=2, ensure_ascii=False), encoding="utf-8")
        save_project(project, proj_path)
        sheet = proj_path.parent / "contact_sheet.html"
        write_contact_sheet(project, per_shot_candidates, sheet, Path(project.get("source_xlsx") or "story_plan.xlsx").name)
        print(f"\n  API requests this run: {used}")
        print(f"  📄  candidates.json  → {cand_path}\n  📄  contact_sheet.html → {sheet}")
        print("  🚦  G4: open the contact sheet, pick per shot, Save picks.json, then --apply-picks <picks.json>\n")
        return

    save_project(project, proj_path)
    xlsx = Path(project.get("source_xlsx", ""))
    if project.get("source_xlsx") and xlsx.exists() and (log_rows or tracker_rows):
        if write_excel_logs(xlsx, log_rows, tracker_rows):
            print(f"\n  ✅  Download Log + Asset Tracker updated in {xlsx.name}")

    print(f"\n{'═'*62}\n  {'❌' if errors else '✅'}  {ok} downloaded  |  {skipped} kept  |  {errors} errors/rejects\n"
          f"  API requests this run: {used}\n  project.json updated\n{'═'*62}\n")
    if errors:
        failed = [s["shot_id"] for s in shots if s.get("status") in ("error", "swap")]
        print(f"  Shots without an asset: {', '.join(failed) or '?'}")
        print("  Next: python validation_report.py project.json   (review)  ·  python prompt_generator.py project.json  (AI fallback)\n")
        sys.exit(2)                                   # run_pipeline stops here instead of rendering a short video


if __name__ == "__main__":
    main()
