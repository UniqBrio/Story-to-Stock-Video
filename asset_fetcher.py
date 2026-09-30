#!/usr/bin/env python3
"""
asset_fetcher.py  (SVOS v2 render layer)
────────────────
For each STOCK shot (T2/T3) in project.json:
  1. Runs EVERY comma-separated keyword phrase as its own query (the v1 bug
     joined them into one over-specific query that usually returned nothing)
     across Pexels Videos → Pixabay Videos → Pexels/Pixabay/Unsplash images.
  2. Scores every candidate: resolution · aspect fit · duration fit · query
     rank · source · portrait-file availability. Rejects clips shorter than the
     shot and anything already used by another shot.
  3. Downloads the winner (or, with --candidates N, writes a CONTACT SHEET for
     G4 approval and downloads nothing until --apply-picks).
  4. Writes local_file / source / license / page_url / score back to project.json
     and logs to the Download Log + Asset Tracker sheets.

Cards (T1/T5) and product inserts (T4) are never fetched.

Usage:
    python asset_fetcher.py project.json                      # auto-pick + download
    python asset_fetcher.py project.json --candidates 8       # contact_sheet.html for G4, no downloads
    python asset_fetcher.py project.json --apply-picks picks.json
    python asset_fetcher.py project.json --refetch            # re-download even if file exists
    python asset_fetcher.py project.json --shot 003

API keys: env PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_API_KEY override the sheet.
"""

import sys
import json
import time
import html
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime

from svos_common import (load_project, save_project, shot_kind, banner, env_key, set_log,
                         assets_dir, output_dir, VIDEO_EXT, IMAGE_EXT, infer_asset_type)

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

RETRY_DELAY = 2
MAX_RETRIES = 3
RATE_DELAY  = 0.35
MAX_QUERIES_PER_SHOT = 6
UA = "UniqBrio-SVOS/2.0"

_key_state = {"pexels_ok": True, "pixabay_ok": True, "unsplash_ok": True}


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _get(url, headers=None, timeout=20):
    hdrs = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, headers=hdrs)
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RETRY_DELAY * (attempt + 1)
                print(f"    ⏳  Rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            return None, f"HTTP {e.code}"
        except Exception as ex:
            time.sleep(RETRY_DELAY)
            last = str(ex)
    return None, f"request failed ({last if 'last' in dir() else 'unknown'})"


def _download(url, dest: Path, timeout=60) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        if tmp.stat().st_size < 10 * 1024:
            print(f"    ❌  Download too small ({tmp.stat().st_size} bytes) — discarding")
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except Exception as e:
        print(f"    ❌  Download failed: {e}")
        tmp.unlink(missing_ok=True)
        return False


# ── Scoring ───────────────────────────────────────────────────────────────────
def _aspect_score(w, h, tw, th) -> int:
    if not w or not h:
        return 0
    diff = abs(w / h - tw / th)
    if diff < 0.05:
        return 30
    if diff < 0.25:
        return 18
    # landscape source for a portrait target: usable only with a heavy crop
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
    """Best rendition of a video: prefer the target orientation, then the largest."""
    if not files:
        return None
    portrait_target = th > tw
    def key(f):
        w, h = f.get("width", 0), f.get("height", 0)
        same_orient = (h > w) == portrait_target
        return (same_orient, _res_score(w, h, tw, th), w * h)
    return max(files, key=key)


def score_candidate(c: dict, shot_dur: float, tw: int, th: int) -> int:
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
    s += max(0, 10 - 3 * c.get("query_rank", 0))          # earlier phrase = more literal
    s += {"pexels": 8, "pixabay": 7, "unsplash": 5}.get(c["source"], 0)
    return int(s)


# ── Source searchers (all return normalised candidate dicts) ──────────────────
def _cand(source, ctype, cid, url, w, h, dur, thumb, page, author, query, rank):
    return {"source": source, "type": ctype, "id": str(cid), "url": url, "width": int(w or 0),
            "height": int(h or 0), "duration": float(dur or 0), "thumb": thumb or "",
            "page_url": page or "", "author": author or "", "query": query, "query_rank": rank,
            "license": LICENSES.get(source, "")}


def search_pexels_videos(query, key, per_page, tw, th, rank):
    if not key or not _key_state["pexels_ok"]:
        return []
    orient = "portrait" if th > tw else "landscape"
    url = f"{PEXELS_VIDEO_URL}?query={urllib.parse.quote(query)}&per_page={per_page}&orientation={orient}"
    data, err = _get(url, {"Authorization": key})
    time.sleep(RATE_DELAY)
    if err and "403" in err or err and "401" in err:
        _key_state["pexels_ok"] = False
        print(f"    ❌  Pexels rejected the API key ({err}). Get a new key at https://www.pexels.com/api/ "
              f"and set PEXELS_API_KEY.")
        return []
    out = []
    for v in (data or {}).get("videos", []):
        f = _pick_file(v.get("video_files", []), tw, th)
        if not f:
            continue
        out.append(_cand("pexels", "video", v.get("id"), f.get("link", ""), f.get("width"), f.get("height"),
                         v.get("duration"), v.get("image", ""), v.get("url", ""),
                         (v.get("user") or {}).get("name", ""), query, rank))
    return out


def search_pixabay_videos(query, key, per_page, tw, th, rank):
    if not key or not _key_state["pixabay_ok"]:
        return []
    url = (f"{PIXABAY_VIDEO_URL}?key={key}&q={urllib.parse.quote(query)}"
           f"&per_page={max(3, per_page)}&video_type=film&safesearch=true")
    data, err = _get(url)
    time.sleep(RATE_DELAY)
    if err and ("400" in err or "401" in err or "403" in err):
        _key_state["pixabay_ok"] = False
        print(f"    ❌  Pixabay rejected the API key ({err}). Set PIXABAY_API_KEY.")
        return []
    out = []
    for hit in (data or {}).get("hits", []):
        files = []
        thumb = ""
        for q, vd in (hit.get("videos") or {}).items():
            if isinstance(vd, dict) and vd.get("url"):
                files.append({"link": vd["url"], "width": vd.get("width", 0), "height": vd.get("height", 0)})
                thumb = thumb or vd.get("thumbnail", "")
        f = _pick_file(files, tw, th)
        if not f:
            continue
        out.append(_cand("pixabay", "video", hit.get("id"), f["link"], f["width"], f["height"],
                         hit.get("duration"), thumb, hit.get("pageURL", ""), hit.get("user", ""), query, rank))
    return out


def search_pexels_images(query, key, per_page, tw, th, rank):
    if not key or not _key_state["pexels_ok"]:
        return []
    orient = "portrait" if th > tw else "landscape"
    url = f"{PEXELS_IMAGE_URL}?query={urllib.parse.quote(query)}&per_page={per_page}&orientation={orient}"
    data, err = _get(url, {"Authorization": key})
    time.sleep(RATE_DELAY)
    out = []
    for p in (data or {}).get("photos", []):
        src = p.get("src", {})
        out.append(_cand("pexels", "image", p.get("id"), src.get("original") or src.get("large2x", ""),
                         p.get("width"), p.get("height"), 0, src.get("medium", ""), p.get("url", ""),
                         p.get("photographer", ""), query, rank))
    return out


def search_pixabay_images(query, key, per_page, tw, th, rank):
    if not key or not _key_state["pixabay_ok"]:
        return []
    orient = "vertical" if th > tw else "horizontal"
    url = (f"{PIXABAY_IMAGE_URL}?key={key}&q={urllib.parse.quote(query)}"
           f"&per_page={max(3, per_page)}&image_type=photo&safesearch=true&orientation={orient}")
    data, err = _get(url)
    time.sleep(RATE_DELAY)
    out = []
    for hit in (data or {}).get("hits", []):
        out.append(_cand("pixabay", "image", hit.get("id"), hit.get("largeImageURL", ""),
                         hit.get("imageWidth"), hit.get("imageHeight"), 0, hit.get("previewURL", ""),
                         hit.get("pageURL", ""), hit.get("user", ""), query, rank))
    return out


def search_unsplash_images(query, key, per_page, tw, th, rank):
    if not key or not _key_state["unsplash_ok"]:
        return []
    orient = "portrait" if th > tw else "landscape"
    url = f"{UNSPLASH_IMAGE_URL}?query={urllib.parse.quote(query)}&per_page={per_page}&orientation={orient}"
    data, err = _get(url, {"Authorization": f"Client-ID {key}"})
    time.sleep(RATE_DELAY)
    if err and ("401" in err or "403" in err):
        _key_state["unsplash_ok"] = False
        print(f"    ❌  Unsplash rejected the API key ({err}).")
        return []
    out = []
    for r in (data or {}).get("results", []):
        urls = r.get("urls", {})
        out.append(_cand("unsplash", "image", r.get("id"), (urls.get("raw", "") + "&w=3000&fm=jpg") if urls.get("raw") else urls.get("full", ""),
                         r.get("width"), r.get("height"), 0, urls.get("small", ""),
                         (r.get("links") or {}).get("html", ""), (r.get("user") or {}).get("name", ""), query, rank))
    return out


# ── Candidate gathering per shot ──────────────────────────────────────────────
def gather_candidates(shot: dict, keys: dict, tw: int, th: int, per_query: int, used_ids: set) -> list[dict]:
    queries = [q for q in shot.get("keywords", []) if q.strip()][:MAX_QUERIES_PER_SHOT]
    if not queries and shot.get("scene_desc"):
        queries = [shot["scene_desc"][:80]]
    priority = shot.get("asset_priority", "video_first")
    want_video = priority in ("video_first", "video_only")
    want_image = priority in ("image_first", "image_only", "video_first")
    shot_dur = float(shot.get("duration", 4.0))

    seen, cands = set(), []
    def add(lst):
        for c in lst:
            k = (c["source"], c["type"], c["id"])
            if k in seen or k in used_ids or not c["url"]:
                continue
            seen.add(k)
            c["score"] = score_candidate(c, shot_dur, tw, th)
            if c["score"] > 0:
                cands.append(c)

    for rank, q in enumerate(queries):
        if want_video:
            add(search_pexels_videos(q, keys.get("pexels", ""), per_query, tw, th, rank))
            add(search_pixabay_videos(q, keys.get("pixabay", ""), per_query, tw, th, rank))
    have_good_video = any(c["type"] == "video" and c["score"] >= 60 for c in cands)
    if want_image and (priority in ("image_first", "image_only") or not have_good_video):
        for rank, q in enumerate(queries):
            add(search_pexels_images(q, keys.get("pexels", ""), per_query, tw, th, rank))
            add(search_pixabay_images(q, keys.get("pixabay", ""), per_query, tw, th, rank))
            add(search_unsplash_images(q, keys.get("unsplash", ""), per_query, tw, th, rank))

    if priority == "video_only":
        cands = [c for c in cands if c["type"] == "video"]
    if priority == "image_only":
        cands = [c for c in cands if c["type"] == "image"]
    cands.sort(key=lambda c: (-c["score"], c["query_rank"]))
    return cands


def slug_for(shot: dict) -> str:
    base = shot["keywords"][0] if shot.get("keywords") else shot.get("scene_desc", "scene")
    s = "_".join(base.split()[:3]).lower()
    return "".join(ch if ch.isalnum() or ch == "_" else "" for ch in s) or "scene"


def download_candidate(shot: dict, c: dict, afolder: Path) -> bool:
    sub = "Videos" if c["type"] == "video" else "Images"
    ext = ".mp4" if c["type"] == "video" else ".jpg"
    dest = afolder / sub / f"shot_{shot['shot_id']}_{slug_for(shot)}{ext}"
    print(f"    ↓   {shot['shot_id']} [{c['type']}] {c['source']}#{c['id']} score={c['score']} "
          f"{c['width']}×{c['height']}{(' ' + str(round(c['duration'], 1)) + 's') if c['duration'] else ''} → {dest.name}")
    if not _download(c["url"], dest):
        return False
    shot.update({
        "asset_type": c["type"], "source": c["source"], "local_file": str(dest), "status": "downloaded",
        "asset_id": c["id"], "asset_url": c["url"], "page_url": c["page_url"], "author": c["author"],
        "license": c["license"], "asset_score": c["score"],
        "asset_resolution": f"{c['width']}x{c['height']}", "asset_duration": c["duration"],
        "query_used": c["query"],
    })
    return True


# ── Contact sheet (G4) ────────────────────────────────────────────────────────
def write_contact_sheet(project: dict, per_shot: dict, out_path: Path):
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
            rows += f"""
            <label class="cand">
              <input type="radio" name="pick_{html.escape(sid)}" value="{i}" {'checked' if i == 0 else ''}
                     onchange="update()">
              {thumb}
              <div class="meta">
                <b>#{i+1} · {html.escape(c['source'])} · {dur}</b>
                <span>{c['width']}×{c['height']} · score {c['score']}</span>
                <span class="q">“{html.escape(c['query'])}”</span>
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
          <div class="kw">{' · '.join(html.escape(k) for k in shot.get('keywords', []))}</div>
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
 .q{{color:#6d5fa0;font-style:italic}} .meta a{{color:#DE7D14;text-decoration:none}}
 .skip{{display:block;margin-top:10px;font-size:12px;color:#c0392b}} .empty{{color:#c0392b}}
 #out{{position:sticky;bottom:0;background:#0a1a0a;border:1px solid #27ae60;border-radius:10px;padding:14px 18px;margin-top:20px}}
 textarea{{width:100%;min-height:90px;background:#0f0f17;color:#c5f0c5;border:1px solid #333;border-radius:6px;font-family:monospace;font-size:12px}}
 button{{background:#DE7D14;color:#fff;border:none;padding:8px 16px;border-radius:6px;font-weight:600;cursor:pointer}}
</style></head><body>
<h1>🎞 Contact Sheet — {name}</h1>
<div class="sub">G4 approval · generated {datetime.now():%d %b %Y %H:%M} · pick one candidate per shot, then copy the picks
 and save as <code>picks.json</code> next to project.json → <code>python asset_fetcher.py project.json --apply-picks picks.json</code></div>
{cards}
<div id="out"><b style="color:#27ae60">picks.json</b> <button onclick="copyPicks()">📋 Copy</button><br>
<textarea id="picks" readonly></textarea></div>
<script>
function update(){{
  const picks={{}};
  document.querySelectorAll('section.shot').forEach(s=>{{
    const sid=s.id.replace('shot_','');
    const skip=s.querySelector('input[name="skip_'+sid+'"]');
    if(skip&&skip.checked){{picks[sid]='reject';return;}}
    const r=s.querySelector('input[name="pick_'+sid+'"]:checked');
    if(r)picks[sid]=parseInt(r.value);
  }});
  document.getElementById('picks').value=JSON.stringify(picks,null,2);
}}
function copyPicks(){{update();const t=document.getElementById('picks');t.select();document.execCommand('copy');}}
update();
</script></body></html>"""
    out_path.write_text(doc, encoding="utf-8")


# ── Excel logging ─────────────────────────────────────────────────────────────
def write_excel_logs(xlsx_path: Path, log_rows: list, tracker_rows: list):
    try:
        wb = openpyxl.load_workbook(xlsx_path)
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
def main():
    ap = argparse.ArgumentParser(description="Fetch stock assets for every stock shot in project.json")
    ap.add_argument("project", help="Path to project.json")
    ap.add_argument("--force", action="store_true", help="(kept for run_pipeline compatibility — does NOT re-download)")
    ap.add_argument("--refetch", action="store_true", help="Re-download even if a file already exists")
    ap.add_argument("--shot", default=None, help="Only this shot ID")
    ap.add_argument("--candidates", type=int, default=0, metavar="N",
                    help="Contact-sheet mode: gather up to N candidates per shot, write contact_sheet.html + candidates.json, download nothing")
    ap.add_argument("--apply-picks", default=None, metavar="PICKS_JSON",
                    help="Download the candidates chosen in the contact sheet")
    args = ap.parse_args()

    proj_path = Path(args.project)
    project = load_project(proj_path)
    set_log(output_dir(project, proj_path) / "logs" / "pipeline_ffmpeg.log")
    keys = {
        "pexels":   env_key("PEXELS_API_KEY",   project.get("api_keys", {}).get("pexels", "")),
        "pixabay":  env_key("PIXABAY_API_KEY",  project.get("api_keys", {}).get("pixabay", "")),
        "unsplash": env_key("UNSPLASH_API_KEY", project.get("api_keys", {}).get("unsplash", "")),
    }
    tw, th = project.get("width", 1080), project.get("height", 1920)
    afolder = assets_dir(project, proj_path)
    shots = project["shots"]
    if args.shot:
        shots = [s for s in shots if s["shot_id"] == args.shot]
        if not shots:
            sys.exit(f"❌  Shot '{args.shot}' not found")

    banner("Asset Fetcher", f"{len(shots)} shots  |  assets → {afolder}",
           "mode: " + ("CONTACT SHEET (no downloads)" if args.candidates else
                       "APPLY PICKS" if args.apply_picks else "auto-pick + download"))
    missing = [k for k, v in keys.items() if not v or "YOUR_" in v]
    if missing:
        print(f"  ⚠️  No API key for: {missing}  (env PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_API_KEY)\n")

    used_ids = {(s.get("source"), s.get("asset_type"), str(s.get("asset_id")))
                for s in project["shots"] if s.get("asset_id")}
    log_rows, tracker_rows = [], []
    ok = skipped = errors = 0
    per_shot_candidates = {}
    picks = None
    if args.apply_picks:
        picks = json.loads(Path(args.apply_picks).read_text(encoding="utf-8-sig"))
        cand_file = proj_path.parent / "candidates.json"
        if not cand_file.exists():
            sys.exit("❌  candidates.json not found — run --candidates first")
        per_shot_candidates = json.loads(cand_file.read_text(encoding="utf-8-sig"))

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
        if local and Path(local).exists() and not args.refetch and not args.candidates and not args.apply_picks:
            if not shot.get("asset_type"):
                shot["asset_type"] = infer_asset_type(local)
            print(f"    ⏭️  {sid} — file already exists, keeping approved asset")
            skipped += 1
            continue

        if args.apply_picks:
            choice = picks.get(sid)
            cands = per_shot_candidates.get(sid, [])
            if choice in (None, "reject") or not cands:
                print(f"    ↩   {sid} — {'rejected at G4' if choice == 'reject' else 'no pick'}; back to storyboard")
                shot["status"] = "swap"
                errors += 1
                continue
            c = cands[int(choice)]
            if download_candidate(shot, c, afolder):
                used_ids.add((c["source"], c["type"], c["id"]))
                ok += 1
            else:
                shot["status"] = "error"; errors += 1
        else:
            per_query = max(3, args.candidates) if args.candidates else 5
            cands = gather_candidates(shot, keys, tw, th, per_query, used_ids)
            if args.candidates:
                per_shot_candidates[sid] = cands[:args.candidates]
                print(f"    🔎  {sid} — {len(cands)} candidates ({sum(1 for c in cands if c['type']=='video')} video)")
                continue
            if not cands:
                print(f"    ❌  {sid} — no suitable asset for {shot.get('keywords', [])[:3]}")
                shot["status"] = "error"; errors += 1
                continue
            downloaded = False
            for c in cands[:3]:                       # fall through to the next best on a dead link
                if download_candidate(shot, c, afolder):
                    used_ids.add((c["source"], c["type"], c["id"]))
                    downloaded = True
                    break
            if downloaded:
                ok += 1
            else:
                shot["status"] = "error"; errors += 1

        log_rows.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid, shot.get("scene_desc", "")[:50],
                         shot.get("source", ""), shot.get("asset_url", ""), shot.get("local_file", ""),
                         shot.get("asset_resolution", ""), shot.get("status", "")])
        if shot.get("status") == "downloaded":
            tracker_rows.append([sid, shot.get("asset_type", ""), shot.get("source", ""),
                                 shot.get("asset_resolution", ""), shot.get("asset_duration", ""),
                                 shot.get("license", ""), shot.get("local_file", ""), "yes",
                                 shot.get("asset_score", ""), "", f"by {shot.get('author','')} · {shot.get('page_url','')}"])

    if args.candidates:
        cand_path = proj_path.parent / "candidates.json"
        cand_path.write_text(json.dumps(per_shot_candidates, indent=2, ensure_ascii=False), encoding="utf-8")
        sheet = proj_path.parent / "contact_sheet.html"
        write_contact_sheet(project, per_shot_candidates, sheet)
        print(f"\n  📄  candidates.json  → {cand_path}\n  📄  contact_sheet.html → {sheet}")
        print("  🚦  G4: open the contact sheet, pick per shot, save picks.json, then --apply-picks picks.json\n")
        return

    save_project(project, proj_path)
    xlsx = Path(project.get("source_xlsx", ""))
    if xlsx.exists() and (log_rows or tracker_rows):
        if write_excel_logs(xlsx, log_rows, tracker_rows):
            print(f"\n  ✅  Download Log + Asset Tracker updated in {xlsx.name}")

    print(f"\n{'═'*62}\n  {'❌' if errors else '✅'}  {ok} downloaded  |  {skipped} kept  |  {errors} errors/rejects\n"
          f"  project.json updated\n{'═'*62}\n")
    if errors:
        failed = [s["shot_id"] for s in shots if s.get("status") in ("error", "swap")]
        print(f"  Shots without an asset: {', '.join(failed) or '?'}")
        print("  Next: python validation_report.py project.json   (review)  ·  python prompt_generator.py project.json  (AI fallback)\n")
        sys.exit(2)                                   # run_pipeline stops here instead of rendering a short video


if __name__ == "__main__":
    main()
