#!/usr/bin/env python3
"""
qa_check.py  (SVOS v2 render layer — Stage 6 automation)
───────────
Runs the machine-checkable half of the G6 QA battery on the final export and
writes Output/qa_report.md + qa_report.json + Output/qa/frame_*.jpg.

  Technical   : resolution · fps · H.264 High · yuv420p · AAC 48 kHz stereo · faststart · length
  Loudness    : integrated LUFS vs target (±1 LU) · true peak
  Picture     : black frames · frozen video (outside T1/T5 cards) · thumb-stop frames at 0/1/2/3 s
  Sound       : dead air > 1.5 s
  Story rules : hook in ≤ 1.5 s · restraint budget (README §2) · reading-time minimums ·
                single CTA · brand linkage (T5 card or bug) · safe zones on · license log complete
  Manual      : sound-off comprehension · phone viewing — listed as reminders, never auto-passed

Verdict: READY FOR G6 (human decides ship/fix) or FIX BEFORE G6 (critical findings).
Exit code 1 on critical findings so run_pipeline can surface them.

Usage:
    python qa_check.py project.json
"""

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

from svos_common import (load_project, save_project, check_ffmpeg, run_ff, run_ff_capture,
                         probe_video_info, banner, set_log, output_dir, shot_kind, words)

CTA_RE = re.compile(r"\b(dm|comment|link|click|tap|follow|visit|call|whatsapp|bio|download|sign\s*up|book|demo)\b", re.I)


class Report:
    def __init__(self):
        self.items = []          # (severity, area, message)
    def add(self, sev, area, msg):
        self.items.append((sev, area, msg))
    def count(self, sev):
        return sum(1 for s, _, _ in self.items if s == sev)


def faststart_ok(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(2 * 1024 * 1024)
        mo, md = head.find(b"moov"), head.find(b"mdat")
        return mo != -1 and (md == -1 or mo < md)
    except Exception:
        return False


def ebur128(path: Path) -> dict:
    rc, err = run_ff_capture(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                              "-f", "null", "-"], "qa ebur128")
    out = {}
    # Only the SUMMARY block (after "Integrated loudness:") — the per-frame lines also print "I: … LUFS"
    summary = err[err.rfind("Integrated loudness:"):] if "Integrated loudness:" in err else ""
    for key, rx in (("integrated", r"Integrated loudness:\s*I:\s*(-?[0-9.]+) LUFS"),
                    ("lra", r"Loudness range:\s*LRA:\s*(-?[0-9.]+) LU"),
                    ("true_peak", r"True peak:\s*Peak:\s*(-?[0-9.]+) dBFS")):
        err = summary or err
        m = re.search(rx, err)
        if m:
            out[key] = float(m.group(1))
    return out


def detect(path: Path, vf: str = "", af: str = "") -> str:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if vf:
        cmd += ["-vf", vf]
    if af:
        cmd += ["-af", af]
    else:
        cmd += ["-an"]
    if vf and not af:
        pass
    cmd += ["-f", "null", "-"]
    rc, err = run_ff_capture(cmd, f"qa detect {vf or af}")
    return err


def overlaps_card(a: float, b: float, timeline: list[dict]) -> bool:
    for t in timeline:
        if t.get("kind") in ("card", "logo_card") and a < t["end"] and b > t["start"]:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Automated QA battery for the final export (G6 prep)")
    ap.add_argument("project", help="Path to project.json")
    args = ap.parse_args()

    check_ffmpeg()
    proj_path = Path(args.project)
    project = load_project(proj_path)
    out_dir = output_dir(project, proj_path)
    set_log(out_dir / "logs" / "pipeline_ffmpeg.log")
    final = Path(project.get("final_output", ""))
    if not final.exists():
        sys.exit("❌  final_output missing — run final_export.py first")

    tw, th, fps = project.get("width", 1080), project.get("height", 1920), project.get("fps", 30)
    audio_cfg, text_cfg, render = project.get("audio", {}), project.get("text", {}), project.get("render", {})
    timeline = project.get("timeline", [])
    manifest = project.get("overlay_manifest", [])
    R = Report()

    banner("QA Check (Stage 6)", f"{final.name}")

    # ── Technical spec ────────────────────────────────────────────────────────
    info = probe_video_info(final)
    if (info["width"], info["height"]) != (tw, th):
        R.add("critical", "spec", f"Resolution {info['width']}×{info['height']} ≠ {tw}×{th}")
    if abs(info["fps"] - fps) > 0.6:
        R.add("major", "spec", f"Frame rate {info['fps']:.2f} ≠ {fps}")
    if info["codec"] != "h264" or "High" not in (info["profile"] or ""):
        R.add("major", "spec", f"Video codec/profile {info['codec']} {info['profile']} (want h264 High)")
    if info["pix_fmt"] != "yuv420p":
        R.add("critical", "spec", f"Pixel format {info['pix_fmt']} (want yuv420p — some phones show green/blank)")
    if not info["has_audio"]:
        R.add("critical", "spec", "No audio stream")
    else:
        if info["audio_codec"] != "aac" or info["sample_rate"] not in (44100, 48000) or info["channels"] != 2:
            R.add("major", "spec", f"Audio {info['audio_codec']} {info['sample_rate']} Hz {info['channels']}ch (want AAC 48 kHz stereo)")
    if not faststart_ok(final):
        R.add("major", "spec", "moov atom is not at the front (faststart) — slow first frame on mobile")
    max_d = float(render.get("max_duration", 90))
    if info["duration"] > max_d:
        R.add("major", "length", f"{info['duration']:.1f}s exceeds the {max_d:.0f}s Reels target")
    planned = project.get("assembled_duration") or project.get("planned_assembled_duration") or 0
    if planned and abs(info["duration"] - planned) > 0.35:
        R.add("major", "length", f"Final is {info['duration']:.2f}s but the timeline says {planned:.2f}s — check dropped shots")

    # ── Loudness ──────────────────────────────────────────────────────────────
    loud = ebur128(final)
    target = float(audio_cfg.get("target_lufs", -14))
    tp_limit = float(audio_cfg.get("true_peak", -1.5))
    if "integrated" in loud:
        diff = loud["integrated"] - target
        if abs(diff) > 2.0:
            R.add("major", "audio", f"Integrated loudness {loud['integrated']:.1f} LUFS is {diff:+.1f} LU from target {target}")
        elif abs(diff) > 1.0:
            R.add("minor", "audio", f"Integrated loudness {loud['integrated']:.1f} LUFS ({diff:+.1f} LU from target)")
        if loud.get("true_peak", -99) > tp_limit + 0.3:
            R.add("major", "audio", f"True peak {loud['true_peak']:.1f} dBTP above {tp_limit} dBTP — clipping risk on phones")
    else:
        R.add("minor", "audio", "Could not measure loudness")

    # ── Picture detections ────────────────────────────────────────────────────
    err = detect(final, vf="blackdetect=d=0.4:pic_th=0.98")
    for m in re.finditer(r"black_start:([0-9.]+) black_end:([0-9.]+)", err):
        a, b = float(m.group(1)), float(m.group(2))
        if not overlaps_card(a, b, timeline):
            R.add("major", "picture", f"Black frames {a:.2f}–{b:.2f}s")
    err = detect(final, vf="freezedetect=n=-50dB:d=2.0")
    starts = [float(x) for x in re.findall(r"freeze_start:\s*([0-9.]+)", err)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*([0-9.]+)", err)]
    for i, a in enumerate(starts):
        b = ends[i] if i < len(ends) else info["duration"]
        if not overlaps_card(a, b, timeline):
            R.add("major", "picture", f"Frozen picture {a:.2f}–{b:.2f}s ({b - a:.1f}s) — a held frame or a still without Ken Burns")

    # ── Dead air ──────────────────────────────────────────────────────────────
    err = detect(final, af="silencedetect=noise=-45dB:d=1.5")
    for m in re.finditer(r"silence_start:\s*([0-9.]+)", err):
        a = float(m.group(1))
        if a < info["duration"] - 0.6:
            R.add("minor", "audio", f"Dead air from {a:.2f}s (>1.5s of near-silence)")

    # ── Story rules ───────────────────────────────────────────────────────────
    if timeline:
        first = timeline[0]
        first_shot = next((s for s in project["shots"] if s["shot_id"] == first["shot_id"]), {})
        first_text = min((m["start"] for m in manifest), default=99)
        if first["kind"] == "logo_card":
            R.add("critical", "hook", "Video opens on the logo card — README §2b bans opening logo splashes; the first 1.5s belong to the hook")
        if first["kind"] == "card" and first_text > 0.6:
            R.add("major", "hook", f"Opening card shows no text until {first_text:.2f}s")
        if first["kind"] == "stock" and first_shot.get("asset_type") == "image" and first_shot.get("ken_burns", "none") == "none" and first_text > 1.5:
            R.add("major", "hook", "Opening shot is a static still with no text in the first 1.5s — nothing moves in the hook window")
        if first_text > 1.5 and first["kind"] != "stock":
            R.add("minor", "hook", f"First on-screen text at {first_text:.2f}s (>1.5s)")

    cards = [t for t in timeline if t["kind"] == "card"]
    kw = [m for m in manifest if m["style"] in ("keyword", "caption")]
    if len(cards) > int(text_cfg.get("card_budget", 3)):
        R.add("major", "restraint", f"{len(cards)} blank text frames > budget {text_cfg.get('card_budget', 3)}")
    if len(kw) > int(text_cfg.get("overlay_budget", 5)):
        R.add("major", "restraint", f"{len(kw)} stock overlays > budget {text_cfg.get('overlay_budget', 5)}")
    for note in project.get("overlay_audit", []):
        R.add("minor", "overlay", note)

    cta_hits = [m for m in manifest if CTA_RE.search(" ".join(m["lines"]))]
    if len(cta_hits) == 0:
        R.add("critical", "cta", "No visible CTA text found (sound-off viewers never see the ask)")
    elif len(cta_hits) > 1:
        R.add("major", "cta", f"{len(cta_hits)} CTA-like overlays ({', '.join(m['shot_id'] for m in cta_hits)}) — single CTA rule")
    elif cta_hits[0]["end"] < info["duration"] - 8:
        R.add("minor", "cta", f"CTA ends at {cta_hits[0]['end']:.1f}s, well before the end — consider moving it to the close")

    has_logo_card = any(t["kind"] == "logo_card" for t in timeline)
    logo_mode = project.get("logo", {}).get("mode", "end_only")
    if not has_logo_card and logo_mode in ("end_only", "none"):
        R.add("major", "brand", "No T5 logo card and no corner bug — brand-linkage test will fail")
    if not text_cfg.get("safe_zones", True):
        R.add("major", "safe-zones", "safe_zones is off — text may sit under Instagram's UI")
    for m in manifest:
        if m.get("position") == "top_center" and m["style"] != "card":
            R.add("minor", "safe-zones", f"{m['shot_id']}: top_center overlays sit near the Reels header — prefer center/lower_third")
    for s in project["shots"]:
        if shot_kind(s) == "stock" and s.get("source") in ("pexels", "pixabay", "unsplash") and not s.get("license"):
            R.add("minor", "license", f"{s['shot_id']}: no license recorded for {s.get('source')} asset")
        if shot_kind(s) == "stock" and s.get("asset_type") == "image" and s.get("ken_burns", "none") == "none":
            R.add("minor", "picture", f"{s['shot_id']}: still image without Ken Burns")

    # ── Thumb-stop frames for the human freeze test ───────────────────────────
    qa_dir = out_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for t in (0.0, 1.0, 2.0, 3.0):
        if t < info["duration"]:
            fp = qa_dir / f"frame_{int(t)}s.jpg"
            if run_ff(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(final), "-frames:v", "1", "-q:v", "2", str(fp)], "qa frame", quiet=True):
                frames.append(str(fp))

    # ── Verdict + report ──────────────────────────────────────────────────────
    crit, major, minor = R.count("critical"), R.count("major"), R.count("minor")
    verdict = "FIX BEFORE G6" if crit else "READY FOR G6 REVIEW"
    md = [f"# QA Report — {project.get('project_name')}",
          f"*{datetime.now():%Y-%m-%d %H:%M} · {final.name} · {info['duration']:.2f}s · {info['width']}×{info['height']} · "
          f"{loud.get('integrated', '?')} LUFS / TP {loud.get('true_peak', '?')} dBTP*", "",
          f"## Verdict: **{verdict}**  — {crit} critical · {major} major · {minor} minor", ""]
    if R.items:
        md.append("| Severity | Area | Finding |\n|---|---|---|")
        for sev in ("critical", "major", "minor"):
            for s, area, msg in R.items:
                if s == sev:
                    md.append(f"| {sev.upper()} | {area} | {msg} |")
    else:
        md.append("No automated findings.")
    md += ["", "## Timeline", "| Shot | Treatment | Start | End | Transition |", "|---|---|---|---|---|"]
    for t in timeline:
        md.append(f"| {t['shot_id']} | {t['treatment']} | {t['start']:.2f} | {t['end']:.2f} | "
                  f"{t['transition_out']}{(' ' + str(t['trans_dur']) + 's') if t['trans_dur'] else ''} |")
    md += ["", "## Overlays", "| Shot | Style | Text | Window | Size | Anim |", "|---|---|---|---|---|---|"]
    for m in manifest:
        md.append(f"| {m['shot_id']} | {m['style']} | {' / '.join(m['lines'])} | {m['start']:.2f}–{m['end']:.2f}s | {m['size']}px | {m['anim']} |")
    md += ["", "## Manual battery (G6 — never auto-passed)",
           "- [ ] **Sound-off comprehension**: watch muted on a phone; problem → solution → CTA must read from visuals + text alone",
           "- [ ] **Thumb-stop freeze test**: open `Output/qa/frame_0s.jpg … frame_3s.jpg` — would you stop scrolling?",
           "- [ ] **Redmi-class phone**: speaker + screen, sound OFF first, then ON",
           "- [ ] **Brand linkage**: could a competitor swap the logo and publish this unchanged? (must be NO)",
           "- [ ] **Asset ↔ narration alignment** per shot (Indian context, no Western office stock)",
           "", f"Frames: {', '.join(Path(f).name for f in frames)}"]
    (out_dir / "qa_report.md").write_text("\n".join(md), encoding="utf-8")
    (out_dir / "qa_report.json").write_text(json.dumps({
        "verdict": verdict, "critical": crit, "major": major, "minor": minor,
        "findings": [{"severity": s, "area": a, "message": m} for s, a, m in R.items],
        "spec": info, "loudness": loud, "frames": frames}, indent=2, ensure_ascii=False), encoding="utf-8")
    project["qa_report"] = str(out_dir / "qa_report.md")
    project["qa_verdict"] = verdict
    save_project(project, proj_path)

    for sev in ("critical", "major", "minor"):
        for s, area, msg in R.items:
            if s == sev:
                icon = {"critical": "❌", "major": "⚠️", "minor": "ℹ️"}[sev]
                print(f"  {icon}  [{area}] {msg}")
    print(f"\n  {'❌' if crit else '✅'}  {verdict}  — {crit} critical · {major} major · {minor} minor")
    print(f"  📄  {out_dir / 'qa_report.md'}\n  🖼   thumb-stop frames → {qa_dir}\n")
    print("  🚦  G6 is a human gate: review on a phone, sound OFF first, then decide SHIP / FIX / KILL.\n")
    if crit:
        sys.exit(1)


if __name__ == "__main__":
    main()
