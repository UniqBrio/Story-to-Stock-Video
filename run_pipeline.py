#!/usr/bin/env python3
"""
run_pipeline.py  (SVOS v2 render layer)
───────────────
Single entry point — runs every phase in sequence.

    python run_pipeline.py story_plan.xlsx

Phases:
    1  story_reader        Excel → project.json (+ validation, restraint audit)
       preflight           Keys · fonts · FFmpeg · referenced files — critical findings stop the run
       vo_aligner          Fit the voiceover to the shots (phrase → shot); asks only when unsure
    2  asset_fetcher       Stock assets (skipped for cards / product inserts)
    3  clip_normaliser     Exact-duration clips · cards · grades · Ken Burns
    4  transition_engine   Single-pass assembly + authoritative timeline
    5  overlay_engine      Text overlays (styles · motion · safe zones · Tamil) + logo
    6  audio_mixer         VO duck map · music · BGM · sting · loudness normalise
    7  final_export        H.264 High · BT.709 · cover frame · render manifest
       content_safety      U-rated gate on every asset, text and audio layer — runs before Phase 3
    8  qa_check            Automated G6 battery → Output/qa_report.md
       content_safety      U-rated gate on the exported video — runs after Phase 7

Optional utilities (run with --only):
    9  validation_report   Review downloaded assets side-by-side (before phase 3)
   10  prompt_generator    AI image prompts for shots with no usable stock
   11  bgm_prompt_generator BGM brief from the story arc

Options:
    --from N          Start at phase N (1–8)
    --only N          Run one phase only
    --force           Re-render phases 3–7 even if outputs exist (never re-downloads assets)
    --refetch         Phase 2: re-download assets even if files exist (approved picks are lost!)
    --skip-fetch      Skip phase 2 (assets already in place)
    --no-qa           Skip phase 8
    --preflight       Run phase 1 + preflight checks only, then stop
    --allow-drop      Phases 3–4: continue when a shot has no source (video gets shorter than the plan)
    --accept-vo       Use the voiceover timing plan even if the aligner asked a question
    --shot ID         Phase 3 only: normalise a single shot
    --contact-sheet N Stage-4 gate: run phase 1, gather N candidates/shot → contact_sheet.html, STOP for G4
    --apply-picks F   Download the G4 picks from F (picks.json), then continue with phases 3–8
    --apply-safety-review F  Apply approve/reject decisions and the sign-off saved from
                      Output/safety/*_report.html (safety_review.json)
    --apply-review F  Apply keep/swap verdicts saved from validation_report.html (review.json),
                      re-fetch the swapped shots, then continue with phases 3–8

Requirements: pip install openpyxl pillow · FFmpeg (with libfreetype/harfbuzz) in PATH
"""

import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PHASES = {
    1:  ("story_reader",         "Parse Excel → project.json"),
    2:  ("asset_fetcher",        "Fetch stock assets"),
    3:  ("clip_normaliser",      "Cards · trim · grade · Ken Burns"),
    4:  ("transition_engine",    "Assemble with transitions (single pass)"),
    5:  ("overlay_engine",       "Text overlays + logo"),
    6:  ("audio_mixer",          "VO duck map · music · loudness"),
    7:  ("final_export",         "Final H.264 export + manifest"),
    8:  ("qa_check",             "Automated QA battery (G6 prep)"),
    9:  ("validation_report",    "Asset validation report   [optional]"),
    10: ("prompt_generator",     "AI image prompt generator [optional]"),
    11: ("bgm_prompt_generator", "BGM music prompt generator [optional]"),
}
CORE = list(range(1, 9))


def run_script(name: str, args: list[str]) -> int:
    script = Path(__file__).parent / f"{name}.py"
    if not script.exists():
        print(f"  ❌  Script not found: {script}")
        return 127
    sys.stdout.flush()                                # keep our banners in order with the child output
    return subprocess.run([sys.executable, str(script)] + args).returncode


def run_preflight(proj_json: Path, extra: list[str]) -> int:
    print(f"\n{'═'*62}\n  PREFLIGHT — keys · fonts · FFmpeg · referenced files\n{'═'*62}")
    return run_script("preflight", [str(proj_json)] + extra)


def render_variant_b(proj_json: Path, project: dict) -> None:
    """A/B test: version B differs only in the CTA style (text ↔ button). Re-runs phases 5–7 plus the
    final safety gate and QA from project_B.json, reusing the assembled footage, voiceover and music."""
    tcfg = dict(project.get("text", {}))
    a_style = (tcfg.get("cta_style") or "text").lower()
    tcfg["cta_style"] = "text" if a_style == "button" else "button"
    out_name = Path(project.get("output_file", "final_video.mp4"))
    b = dict(project, variant="B", text=tcfg, output_file=f"{out_name.stem}_B{out_name.suffix or '.mp4'}")
    for k in ("overlaid_file", "overlay_manifest", "overlay_audit", "audio_mixed_file", "audio_report", "final_output",
              "cover_frame", "render_manifest", "qa_report", "qa_verdict"):
        b.pop(k, None)
    st = dict(b.get("safety_state", {}))
    st.pop("final", None); st.pop("signoff", None)                 # B needs its own final check and sign-off
    b["safety_state"] = st
    pb = proj_json.with_name("project_B.json")
    pb.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'█'*62}\n  A/B VARIANT B — CTA style '{tcfg['cta_style']}' (A uses '{a_style}')\n{'█'*62}")
    for phase in (5, 6, 7):
        banner(phase, PHASES[phase][1] + "  [B]")
        if run_script(PHASES[phase][0], [str(pb), "--force"]) != 0:
            print(f"\n  ❌  Variant B stopped at phase {phase} — version A is unaffected")
            return
    print(f"\n{'═'*62}\n  CONTENT SAFETY — U-rated check of version B\n{'═'*62}")
    run_script("content_safety", [str(pb), "--stage", "final"])
    banner(8, PHASES[8][1] + "  [B]")
    run_script("qa_check", [str(pb)])
    rb = json.loads(pb.read_text(encoding="utf-8-sig"))
    print("\n" + "█"*62)
    print(f"  A/B  A: {project.get('final_output')}  (CTA {a_style})")
    print(f"       B: {rb.get('final_output', '—')}  (CTA {tcfg['cta_style']})  → QA: {rb.get('qa_verdict', '?')}")
    print("  Post them a few days apart; compare DMs + comments per 1,000 views.")
    print("  Version B needs its own sign-off: Output/safety/final_report_B.html, then")
    print(f"     python content_safety.py {pb.name} --apply-review <safety_review.json>")
    print("█"*62 + "\n")


def run_vo_aligner(proj_json: Path, accept: bool) -> int:
    print(f"\n{'═'*62}\n  VOICEOVER — fit the spoken phrases to the shots\n{'═'*62}")
    rc = run_script("vo_aligner", [str(proj_json)] + (["--accept"] if accept else []))
    if rc == 3:
        print("\n  ❔  Answer the question above (fill VO Line in the sheet), or rerun with --accept-vo")
    return rc


def banner(phase: int, label: str):
    print(f"\n{'═'*62}\n  PHASE {phase} — {label}\n{'═'*62}")


def check_deps():
    errors = []
    for mod, hint in (("openpyxl", "pip install openpyxl"), ("PIL", "pip install pillow  (text auto-fit)")):
        try:
            __import__(mod)
        except ImportError:
            errors.append(f"{mod:<10} →  {hint}")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            errors.append(f"{tool:<10} →  https://ffmpeg.org/download.html (add to PATH)")
    hard = [e for e in errors if not e.startswith("PIL")]
    if errors:
        print("\n  " + ("❌" if hard else "⚠️") + "  Dependencies:")
        for e in errors:
            print(f"     • {e}")
    if hard:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Story-to-Stock-Video pipeline — one Excel → one MP4 + QA report")
    ap.add_argument("xlsx", help="Path to story_plan.xlsx")
    ap.add_argument("--from", dest="from_phase", type=int, default=1, metavar="PHASE")
    ap.add_argument("--only", dest="only_phase", type=int, default=None, metavar="PHASE")
    ap.add_argument("--force", action="store_true", help="Re-render phases 3–7 (never re-downloads)")
    ap.add_argument("--refetch", action="store_true", help="Phase 2: re-download assets even if present")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--no-qa", action="store_true")
    ap.add_argument("--preflight", action="store_true", help="Run phase 1 + preflight checks, then stop")
    ap.add_argument("--allow-drop", action="store_true", help="Phases 3–4: tolerate shots with no source")
    ap.add_argument("--accept-vo", action="store_true", help="Accept the VO timing plan even if it asks a question")
    ap.add_argument("--shot", default=None)
    ap.add_argument("--contact-sheet", type=int, default=0, metavar="N")
    ap.add_argument("--apply-picks", default=None, metavar="PICKS_JSON")
    ap.add_argument("--apply-review", default=None, metavar="REVIEW_JSON")
    ap.add_argument("--apply-safety-review", default=None, metavar="SAFETY_REVIEW_JSON")
    args = ap.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        sys.exit(f"❌  Excel file not found: {xlsx_path}")
    if args.only_phase is not None and args.only_phase not in PHASES:
        sys.exit(f"❌  Invalid --only phase {args.only_phase}. Valid: {sorted(PHASES)}")
    if not (1 <= args.from_phase <= 8):
        sys.exit("❌  --from must be 1–8")
    for flag, val in (("--apply-picks", args.apply_picks), ("--apply-review", args.apply_review)):
        if val and not Path(val).exists():
            sys.exit(f"❌  {flag}: file not found: {val}")
        if val and (args.from_phase > 2 or args.skip_fetch or (args.only_phase and args.only_phase != 2)):
            sys.exit(f"❌  {flag} is applied in Phase 2 — run without --from 3+/--skip-fetch/--only")

    print("\n" + "█"*62 + "\n  UniqBrio — Story to Stock Video Pipeline (SVOS v2 render layer)"
          "\n  One Excel file → one final MP4 + QA report\n" + "█"*62)
    check_deps()

    proj_json = xlsx_path.parent / "project.json"
    force = ["--force"] if args.force else []
    drop = ["--allow-drop"] if args.allow_drop else []

    if args.apply_safety_review:
        if not Path(args.apply_safety_review).exists():
            sys.exit(f"❌  --apply-safety-review: file not found: {args.apply_safety_review}")
        if not proj_json.exists():
            sys.exit("❌  project.json not found — run the pipeline once before applying a safety review")
        print(f"\n{'═'*62}\n  CONTENT SAFETY — applying your decisions\n{'═'*62}")
        if run_script("content_safety", [str(proj_json), "--apply-review", args.apply_safety_review]) != 0:
            sys.exit(1)

    # ── Stage-4 gate flow ─────────────────────────────────────────────────────
    if args.contact_sheet:
        banner(1, PHASES[1][1])
        if run_script("story_reader", [str(xlsx_path), "--out", str(proj_json)]) != 0:
            sys.exit(1)
        if run_preflight(proj_json, ["--need-keys"]) != 0:
            sys.exit(1)
        if run_vo_aligner(proj_json, args.accept_vo) != 0:       # shot lengths decide which clips are long enough
            sys.exit(1)
        banner(2, "Gather candidates → contact sheet (G4)")
        rc = run_script("asset_fetcher", [str(proj_json), "--candidates", str(args.contact_sheet)])
        sys.exit(rc)

    if args.preflight:
        banner(1, PHASES[1][1])
        if run_script("story_reader", [str(xlsx_path), "--out", str(proj_json)]) != 0:
            sys.exit(1)
        sys.exit(run_preflight(proj_json, ["--no-fetch"] if args.skip_fetch else []))

    if args.only_phase:
        phases = [args.only_phase]
    else:
        phases = [p for p in CORE if p >= args.from_phase]
        if args.skip_fetch and 2 in phases:
            phases.remove(2)
        if args.no_qa and 8 in phases:
            phases.remove(8)

    t_start = time.time()
    failed = None
    qa_rc = 0
    run_log = []
    need_preflight = not args.only_phase and args.from_phase <= 2
    safety_final_rc = None
    for phase in phases:
        # preflight + timing first: the timing step can lengthen shots, which changes the clip windows
        # the safety gate clears — so the gate must always see the final shot lengths
        if need_preflight and phase > 1:
            need_preflight = False
            rc = run_preflight(proj_json, ["--no-fetch"] if args.skip_fetch else [])
            run_log.append({"phase": "preflight", "script": "preflight", "rc": rc, "seconds": 0})
            if rc != 0:
                print("\n  ❌  Preflight found critical issues — nothing was fetched or rendered")
                failed = "preflight"
                break
            rc = run_vo_aligner(proj_json, args.accept_vo)
            run_log.append({"phase": "vo-align", "script": "vo_aligner", "rc": rc, "seconds": 0})
            if rc != 0:
                failed = "vo"
                break
        if phase == 3:
            print(f"\n{'═'*62}\n  CONTENT SAFETY — U-rated check of every asset, text and audio layer\n{'═'*62}")
            src = run_script("content_safety", [str(proj_json), "--stage", "assets"])
            run_log.append({"phase": "safety-assets", "script": "content_safety", "rc": src, "seconds": 0})
            if src != 0:
                failed = "safety"
                print({1: "\n  ❌  Content that is not suitable for children was found — nothing was rendered.\n"
                           "     Stock clips that failed were rejected automatically: rerun with --from 2 to fetch replacements.\n"
                           "     Replace any of your own files that failed, then rerun.",
                       3: "\n  ⚠️  Some items need your decision before rendering. Open Output/safety/assets_report.html,\n"
                          "     Approve / Reject, Save safety_review.json, then rerun with\n"
                          f"     --apply-safety-review <safety_review.json> --from 3",
                       4: "\n  ❌  Content-safety models are missing: python content_safety.py --setup"}.get(src, ""))
                break
        script, label = PHASES[phase]
        banner(phase, label)
        t0 = time.time()
        if phase == 1:
            pa = [str(xlsx_path), "--out", str(proj_json)]
        elif phase == 2:
            pa = [str(proj_json)]
            if args.apply_picks:
                pa += ["--apply-picks", args.apply_picks]
            if args.apply_review:
                pa += ["--apply-review", args.apply_review]
            if args.refetch and not args.apply_picks:
                pa += ["--refetch"]
        elif phase == 3:
            pa = [str(proj_json)] + (["--shot", args.shot] if args.shot else []) + force + drop
        elif phase == 4:
            pa = [str(proj_json)] + force + drop
        elif phase in (5, 6, 7):
            pa = [str(proj_json)] + force
        else:
            pa = [str(proj_json)]
        rc = run_script(script, pa)
        dt = time.time() - t0
        run_log.append({"phase": phase, "script": script, "rc": rc, "seconds": round(dt, 1)})
        if rc == 0:
            print(f"\n  ✅  Phase {phase} done  ({dt:.0f}s)")
            if phase == 7:
                print(f"\n{'═'*62}\n  CONTENT SAFETY — U-rated check of the exported video\n{'═'*62}")
                safety_final_rc = run_script("content_safety", [str(proj_json), "--stage", "final"])
                run_log.append({"phase": "safety-final", "script": "content_safety", "rc": safety_final_rc, "seconds": 0})
        elif phase == 8:
            qa_rc = rc
            print(f"\n  ⚠️  Phase 8 found critical QA issues — see Output/qa_report.md")
        elif phase == 2:
            print(f"\n  ❌  Phase 2 finished with missing assets — nothing was rendered.\n"
                  f"     Review: python validation_report.py {proj_json.name}  ·  AI fallback: python prompt_generator.py {proj_json.name}")
            failed = phase
            break
        else:
            print(f"\n  ❌  Phase {phase} FAILED — pipeline stopped")
            failed = phase
            break

    mins, secs = divmod(int(time.time() - t_start), 60)
    project = {}
    if proj_json.exists():
        try:
            project = json.loads(proj_json.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    out_folder = Path(project.get("output_folder", xlsx_path.parent / "Output"))
    try:
        (out_folder / "logs").mkdir(parents=True, exist_ok=True)
        with open(out_folder / "logs" / "pipeline_runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "xlsx": str(xlsx_path),
                                "phases": run_log, "failed": failed}) + "\n")
    except Exception:
        pass

    print("\n" + "█"*62)
    if failed == "preflight":
        print("  ❌  Pipeline stopped at PREFLIGHT — fix the critical items above, then rerun")
        sys.exit(1)
    if failed == "vo":
        print("  ❔  Stopped before rendering — the voiceover aligner has a question (see above / Output/vo_timing.md)")
        sys.exit(3)
    if failed == "safety":
        print("  🛡   Pipeline stopped at the CONTENT-SAFETY gate — see Output/safety/assets_report.html")
        sys.exit(1)
    if failed:
        print(f"  ❌  Pipeline stopped at Phase {failed}: {PHASES[failed][1]}")
        print(f"     Fix the issue above and resume with:\n     python run_pipeline.py {xlsx_path.name} --from {failed}")
        sys.exit(1)
    print(f"  ✅  {'ALL PHASES COMPLETE' if not args.only_phase else 'PHASE COMPLETE'}  ({mins}m {secs}s)")
    if project.get("final_output"):
        print(f"  Output    : {project['final_output']}")
    if project.get("cover_frame"):
        print(f"  Cover     : {project['cover_frame']}")
    if project.get("qa_report"):
        print(f"  QA report : {project['qa_report']}   → verdict: {project.get('qa_verdict', '?')}")
    st = project.get("safety_state", {})
    if safety_final_rc is not None or st.get("final"):
        fin = st.get("final", {})
        signed = bool(st.get("signoff")) and st["signoff"].get("video_sha") == fin.get("video_sha")
        print(f"  Safety    : {fin.get('status', '?')}  ·  sign-off {'recorded — ' + st['signoff']['name'] if signed else 'MISSING — do not publish'}"
              f"\n              {fin.get('report', '')}")
    print("\n  🚦  G6: review on a phone with sound OFF first, then SHIP / FIX / KILL.")
    print("  🛡   Publish only after the U-rated sign-off in Output/safety/final_report.html is recorded.")
    print("█"*62 + "\n")
    if project.get("text", {}).get("ab_variant") and not args.only_phase and project.get("final_output"):
        render_variant_b(proj_json, project)
    sys.exit(qa_rc or (1 if safety_final_rc not in (None, 0) else 0))


if __name__ == "__main__":
    main()
