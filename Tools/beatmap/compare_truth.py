#!/usr/bin/env python3
"""Score an analysis.json against a ground-truth file (from make_test_song.py).

    python3 Tools/beatmap/compare_truth.py Tools/out/test_song_analysis.json Tools/out/test_song_truth.json

Exit code 1 when BPM is off by more than 1, a boundary is more than one beat off,
or fewer than 90% of beats / downbeats are found. With a tempo-change truth file the verdict is
the tempo change (within one beat), each tempo (+-1 BPM or an exact octave, flagged), beats,
downbeats and, with --cues, the drop; section boundaries are listed but do not gate it.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np


def nearest(values, t):
    values = np.asarray(values)
    i = int(np.argmin(np.abs(values - t)))
    return float(values[i]), float(values[i] - t)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis")
    ap.add_argument("truth")
    ap.add_argument("--tol", type=float, default=0.07, help="beat match tolerance in seconds")
    ap.add_argument("--cues", help="cue JSON made with --style slowburn: also score its drop time")
    args = ap.parse_args()
    a = json.load(open(args.analysis))
    tr = json.load(open(args.truth))
    ok = True

    print("| item | truth | estimate | error |")
    print("|---|---|---|---|")
    multi = len(tr.get("tempo_segments", [])) > 1
    if not multi:
        bpm_err = a["bpm"] - tr["bpm"]
        ok &= abs(bpm_err) <= 1.0
        print(f"| BPM | {tr['bpm']:.2f} | {a['bpm']:.2f} | {bpm_err:+.2f} |")
    else:
        est = a.get("tempo_segments", [])
        if len(est) != len(tr["tempo_segments"]):
            ok = False
            print(f"| tempo segments | {len(tr['tempo_segments'])} | {len(est)} | count differs |")
        for k, g in enumerate(tr["tempo_segments"]):
            if k < len(est):
                e = est[k]
                if k > 0:
                    d = e["start"] - g["start"]
                    ok &= abs(d) <= tr["beat_period"] + 1e-6
                    print(f"| tempo change {k} | {g['start']:.2f} s | {e['start']:.2f} s | {d:+.2f} s |")
                r = e["bpm"] / g["bpm"]
                octave = min(abs(r - 2), abs(r - 0.5)) < 0.02
                exact = abs(e["bpm"] - g["bpm"]) <= 1.0
                ok &= exact or octave
                note = f"{e['bpm'] - g['bpm']:+.2f}" if exact else (f"x{r:.2f} (octave: same grid, every 2nd beat)"
                                                                    if octave else f"{e['bpm'] - g['bpm']:+.2f}")
                print(f"| tempo {k + 1} ({g['start']:.0f}-{g['end']:.0f} s) | {g['bpm']:.2f} | {e['bpm']:.2f} | {note} |")

    # beats inside the drummed / pulsed region (truth beats before the outro fade)
    end = tr["final_hit"]
    tb = [t for t in tr["beats"] if t <= end]
    eb = np.array(a["beats"])
    errs = [nearest(eb, t)[1] for t in tb]
    hit = [e for e in errs if abs(e) <= args.tol]
    recall = len(hit) / len(tb)
    ok &= recall >= 0.9
    print(f"| beats found (±{args.tol * 1000:.0f} ms) | {len(tb)} | {len(hit)} | recall {recall:.1%}, "
          f"mean {np.mean(hit) * 1000:+.1f} ms, max {np.max(np.abs(hit)) * 1000:.1f} ms |")
    tdb = [t for t in tr["downbeats"] if t <= end]
    ed = np.array(a["downbeats"])
    dhit = [t for t in tdb if abs(nearest(ed, t)[1]) <= args.tol]
    ok &= len(dhit) / len(tdb) >= 0.9
    edb = [t for t in ed if t <= end]
    prec = sum(1 for t in edb if abs(nearest(tdb, t)[1]) <= args.tol) / max(1, len(edb))
    print(f"| downbeats (bar starts) | {len(tdb)} | {len(dhit)} | recall {len(dhit) / len(tdb):.1%}, "
          f"precision {prec:.1%} |")

    est_b = a["boundaries"]
    for sec, t in zip(tr["sections"][1:], tr["boundaries"]):
        if est_b:
            v, e = nearest(est_b, t)
            hit_ok = abs(e) <= tr["beat_period"] + 1e-6
            if not multi:  # with a tempo change, only the tempo change and the drop gate the verdict
                ok &= hit_ok
            print(f"| boundary -> {sec['name']} | {t:.2f} s | {v:.2f} s | {e:+.2f} s{'' if hit_ok else ' (missed)'} |")
        else:
            ok = False
            print(f"| boundary -> {sec['name']} | {t:.2f} s | none | - |")
    extra = [b for b in est_b if min(abs(b - t) for t in tr["boundaries"]) > tr["beat_period"]]
    if not multi:
        ok &= not extra
    print(f"| extra boundaries | 0 | {len(extra)} | {extra if extra else ''} |")
    if args.cues and "drop" in tr:
        show = json.load(open(args.cues))
        d = show.get("drop")
        if d is None:
            ok = False
            print(f"| drop | {tr['drop']:.2f} s | none | cue file has no drop |")
        else:
            ok &= abs(d - tr["drop"]) <= tr["beat_period"] + 1e-6
            print(f"| drop (slowburn) | {tr['drop']:.2f} s | {d:.2f} s | {d - tr['drop']:+.2f} s |")

    if a["hits"]:
        best = max(a["hits"], key=lambda h: h["strength"] if abs(h["t"] - tr["final_hit"]) < 5 else -1)
        print(f"| strongest hit near final hit | {tr['final_hit']:.2f} s | {best['t']:.2f} s | "
              f"{best['t'] - tr['final_hit']:+.3f} s |")
    print()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
