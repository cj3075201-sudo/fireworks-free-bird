#!/usr/bin/env python3
"""One command from a song file to a validated show: analyse -> choreograph -> validate.

    python3 Tools/beatmap/run_all.py <audio> --credit "<text>" [--out Assets/Fireworks/Show/cues.json]
                                     [--style slowburn [--drop S] [--climax S]] [--tempo-split S]
                                     [--seed N] [--bpm 128 | --bpm 60,140] [--labels intro,verse,...]
                                     [--boundaries 16.3,32.3,...] [--no-copy] [--plot]

Also puts the audio into Assets/Fireworks/Audio/Music/ so the cue file's "music" name matches what
Unity loads: WAV/OGG are copied as is, mp3/m4a/aac/flac/... are converted to Ogg Vorbis (q6, cover art
dropped) and the analysis reads that .ogg, so both see the same samples (mp3 decoders disagree about
encoder delay). If the given file was the project's own copy in Audio/Music, it is removed after
conversion; files outside the project are never touched. Intermediate files go to Tools/out/<song>_analysis.json and,
with --plot, Tools/out/<cues file stem>_timeline.png. Exit code 1 if validation fails.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
import choreograph  # noqa: E402
import validate_cues  # noqa: E402

from audio_io import find_ffmpeg  # noqa: E402

ROOT = HERE.parents[1]
# Formats Unity and the analysis would decode differently (mp3 encoder delay / padding handling
# varies between decoders): convert to Ogg Vorbis once so both read the very same samples.
CONVERT_TO_OGG = {".mp3", ".m4a", ".aac", ".flac", ".opus", ".webm", ".wma"}


def to_ogg(src: Path, dst: Path, quality: float) -> None:
    exe = find_ffmpeg()
    if exe is None:
        raise SystemExit("ffmpeg not found; needed to convert to .ogg (or pass --keep-format)")
    cmd = [exe, "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
           "-c:a", "libvorbis", "-q:a", str(quality), str(dst)]
    subprocess.run(cmd, check=True)
MUSIC_DIR = ROOT / "Assets" / "Fireworks" / "Audio" / "Music"
OUT_DIR = ROOT / "Tools" / "out"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--credit", required=True, help='e.g. "Music: Title - Artist (license)"')
    ap.add_argument("--out", default=str(ROOT / "Assets" / "Fireworks" / "Show" / "cues.json"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bpm", default=None,
                    help="known tempo, if the estimate is wrong; comma list = one per tempo segment (60,140)")
    ap.add_argument("--tempo-split", default=None,
                    help="time(s) in seconds where the tempo changes, comma-separated ('none' = no auto detection)")
    ap.add_argument("--style", choices=["default", "slowburn"], default="default",
                    help="slowburn: sparse until the drop, a full-sky salvo on it, then stepwise growth")
    ap.add_argument("--drop", type=float, default=None, help="slowburn: drop time in seconds (default: estimated)")
    ap.add_argument("--climax", type=float, default=None, help="slowburn: final big hit in seconds (default: estimated)")
    ap.add_argument("--labels", default=None, help="override section names, comma-separated, one per section")
    ap.add_argument("--boundaries", default=None, help="replace detected section starts, e.g. 16.3,32.3,56.3")
    ap.add_argument("--no-copy", action="store_true", help="do not copy the audio into Assets/Fireworks/Audio/Music/")
    ap.add_argument("--keep-format", action="store_true",
                    help="copy mp3/m4a/... as is instead of converting to Ogg Vorbis")
    ap.add_argument("--ogg-quality", type=float, default=6.0, help="Vorbis quality for the conversion (default 6)")
    ap.add_argument("--plot", action="store_true", help="also draw Tools/out/<cues stem>_timeline.png (needs matplotlib)")
    args = ap.parse_args()

    src = Path(args.audio).resolve()
    if not src.exists():
        print(f"ERROR: {src} not found")
        return 1
    stem = src.stem

    # 1) put the song where Unity expects it; compressed formats become Ogg Vorbis, and the
    #    analysis then reads that same .ogg, so cue times and Unity playback share one time axis
    convert = src.suffix.lower() in CONVERT_TO_OGG and not args.keep_format
    if convert:
        dst = (MUSIC_DIR if not args.no_copy else OUT_DIR) / f"{stem}.ogg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        to_ogg(src, dst, args.ogg_quality)
        print(f"[convert] {src.name} -> {dst.relative_to(ROOT)} (Ogg Vorbis q{args.ogg_quality:g}, cover art dropped)")
        if not args.no_copy and src.parent.resolve() == MUSIC_DIR.resolve():
            src.unlink()  # the project copy only; a source outside the project is never touched
            meta = src.with_name(src.name + ".meta")
            if meta.exists():
                meta.unlink()
            print(f"[convert] removed the project copy {src.name} (and its .meta)")
        src = dst.resolve()
    elif not args.no_copy:
        dst = MUSIC_DIR / src.name
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        if dst.resolve() != src:
            shutil.copy2(src, dst)
            print(f"[copy] {src.name} -> {dst.relative_to(ROOT)}")
        else:
            print(f"[copy] already in {dst.parent.relative_to(ROOT)}")

    # 2) analyse
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    analysis = analyze.analyze(src, analyze.parse_bpm(args.bpm), analyze.parse_splits(args.tempo_split))
    a_path = OUT_DIR / f"{stem}_analysis.json"
    a_path.write_text(json.dumps(analysis, indent=1))
    print(f"[analyze] {analysis['duration']:.1f} s, {analysis['bpm']:.2f} BPM, {len(analysis['beats'])} beats, "
          f"{len(analysis['segments'])} sections, {len(analysis['hits'])} strong hits -> {a_path.relative_to(ROOT)}")
    if len(analysis["tempo_segments"]) > 1:
        print("[analyze] tempo: " + " | ".join(f"{g['start']:.1f}-{g['end']:.1f} s {g['bpm']} BPM"
                                               for g in analysis["tempo_segments"]))

    # 3) choreograph
    labels = [x.strip() for x in args.labels.split(",")] if args.labels else None
    bnds = [float(x) for x in args.boundaries.split(",")] if args.boundaries else None
    show = choreograph.choreograph(analysis, src.name, args.credit, args.seed, labels, bnds, args.style, args.drop,
                                   args.climax)
    out = Path(args.out)
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(choreograph.dump(show), encoding="utf-8")
    print(f"[choreograph] {len(show['cues'])} cues -> {out}" + (f"  (style {show['style']}, drop {show['drop']:.2f} s)"
                                                                  if "drop" in show else ""))
    for s in show["sections"]:
        print(f"    {s['name']:<7} {s['start']:7.2f} - {s['end']:7.2f}  energy {s['energy']:.2f}")

    # 4) validate
    err, warn, stats = validate_cues.validate(show, analysis, None if args.no_copy else MUSIC_DIR)
    for w in warn:
        print(f"[validate] WARN  {w}")
    for e in err:
        print(f"[validate] ERROR {e}")
    print(f"[validate] {'OK' if not err else 'FAIL'}: {stats.get('cues')} cues, peak "
          f"{stats.get('peak_per_window')} per second, {stats.get('hits')} hits, "
          f"peak particle load {stats.get('peak_load')}")

    if args.plot:
        import plot_timeline
        plot_timeline.plot(analysis, show, OUT_DIR / f"{out.stem}_timeline.png")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
