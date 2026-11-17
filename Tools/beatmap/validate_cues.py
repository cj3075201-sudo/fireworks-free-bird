#!/usr/bin/env python3
"""Check a cue JSON against the contract in Docs/설계.md (plus the show rules).

    python3 Tools/beatmap/validate_cues.py <cues.json> [--analysis <analysis.json>] [--music-dir DIR]

Errors (exit code 1): missing/unknown-typed fields, unknown type or palette, out-of-range
x/z/h/scale, unsorted t, t outside the song, bad sections (names, order, gaps, coverage),
fountain without a positive dur or running past the end, non-fountain with h != 0 rules,
first cue before 1 s, more than 6 aerial cues in any 1 s window (12 in the finale and in the
slowburn drop eruption, "drop".."drop_end"), particle load above contract.LOAD_CAP.
With --analysis: every t must be a beat or strong onset (+-10 ms).
Warnings: unknown fields (Unity ignores them), same type twice in a row from the same spot,
music file not found.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract as C  # noqa: E402


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(doc: dict, analysis: dict | None = None, music_dir: Path | None = None) -> tuple[list[str], list[str], dict]:
    err: list[str] = []
    warn: list[str] = []

    for k in C.TOP_REQUIRED:
        if k not in doc:
            err.append(f"missing top-level field '{k}'")
    for k in doc:
        if k not in C.TOP_REQUIRED and k not in C.TOP_OPTIONAL:
            warn.append(f"unknown top-level field '{k}' (Unity ignores it)")
    if err:
        return err, warn, {}
    if doc["version"] != C.VERSION:
        err.append(f"version must be {C.VERSION}")
    if not isinstance(doc["music"], str) or not doc["music"]:
        err.append("music must be a non-empty file name")
    elif music_dir is not None and not (music_dir / doc["music"]).exists():
        warn.append(f"music file not found: {music_dir / doc['music']}")
    if not isinstance(doc["credit"], str):
        err.append("credit must be a string")
    dur = doc["duration"]
    if not is_num(dur) or dur <= 0:
        err.append("duration must be a positive number")
        return err, warn, {}
    if not is_num(doc["bpm"]) or doc["bpm"] <= 0:
        err.append("bpm must be a positive number")

    secs = doc["sections"]
    if not isinstance(secs, list) or not secs:
        err.append("sections must be a non-empty list")
        secs = []
    for i, s in enumerate(secs):
        where = f"sections[{i}]"
        for k in ("name", "start", "end", "energy"):
            if k not in s:
                err.append(f"{where}: missing '{k}'")
        if any(k not in s for k in ("name", "start", "end", "energy")):
            continue
        if s["name"] not in C.SECTION_NAMES:
            err.append(f"{where}: name '{s['name']}' not in {C.SECTION_NAMES}")
        if not (is_num(s["start"]) and is_num(s["end"]) and s["start"] < s["end"]):
            err.append(f"{where}: needs start < end")
        if not (is_num(s["energy"]) and 0.0 <= s["energy"] <= 1.0):
            err.append(f"{where}: energy must be within 0..1")
        if i == 0 and abs(s["start"]) > 1e-3:
            err.append("sections must start at 0")
        if i > 0 and is_num(s["start"]) and abs(s["start"] - secs[i - 1].get("end", -1)) > 1e-3:
            err.append(f"{where}: starts at {s['start']} but the previous section ends at {secs[i - 1].get('end')}")
    if secs and is_num(secs[-1].get("end")) and abs(secs[-1]["end"] - dur) > 0.05:
        err.append(f"last section ends at {secs[-1]['end']} but duration is {dur}")

    cues = doc["cues"]
    if not isinstance(cues, list) or not cues:
        err.append("cues must be a non-empty list")
        return err, warn, {}
    prev_t = -1.0
    aerial_t = []
    for i, c in enumerate(cues):
        where = f"cues[{i}] (t={c.get('t')})"
        missing = [k for k in C.CUE_REQUIRED if k not in c]
        if missing:
            err.append(f"{where}: missing {missing}")
            continue
        for k in c:
            if k not in C.CUE_REQUIRED and k not in C.CUE_OPTIONAL:
                warn.append(f"{where}: unknown field '{k}'")
        for k in ("t", "x", "z", "h", "scale"):
            if not is_num(c[k]):
                err.append(f"{where}: '{k}' must be a finite number")
        if any(not is_num(c[k]) for k in ("t", "x", "z", "h", "scale")):
            continue
        t, typ = c["t"], c["type"]
        if typ not in C.TYPES:
            err.append(f"{where}: unknown type '{typ}'")
        if c["palette"] not in C.PALETTES:
            err.append(f"{where}: unknown palette '{c['palette']}'")
        if t < prev_t:
            err.append(f"{where}: cues are not sorted by t")
        prev_t = t
        if not (0.0 <= t <= dur):
            err.append(f"{where}: t outside 0..duration")
        if not (C.X_RANGE[0] <= c["x"] <= C.X_RANGE[1]):
            err.append(f"{where}: x {c['x']} outside {C.X_RANGE}")
        if not (C.Z_RANGE[0] <= c["z"] <= C.Z_RANGE[1]):
            err.append(f"{where}: z {c['z']} outside {C.Z_RANGE}")
        if not (C.SCALE_RANGE[0] <= c["scale"] <= C.SCALE_RANGE[1]):
            err.append(f"{where}: scale {c['scale']} outside {C.SCALE_RANGE}")
        if "hit" in c and not isinstance(c["hit"], bool):
            err.append(f"{where}: hit must be true/false")
        if typ == "fountain":
            if c["h"] != 0:
                err.append(f"{where}: fountain needs h = 0")
            if "dur" not in c or not is_num(c["dur"]) or c["dur"] <= 0:
                err.append(f"{where}: fountain needs a positive 'dur'")
            elif t + c["dur"] > dur + 1e-3:
                err.append(f"{where}: fountain runs past the end of the song")
        else:
            if not (C.H_RANGE[0] <= c["h"] <= C.H_RANGE[1]):
                err.append(f"{where}: h {c['h']} outside {C.H_RANGE}")
            if "dur" in c:
                warn.append(f"{where}: 'dur' is only used by fountain")
            aerial_t.append(t)

    if cues and is_num(cues[0].get("t")) and min(c["t"] for c in cues if is_num(c.get("t"))) < C.FIRST_CUE_MIN_T:
        err.append(f"first cue is before {C.FIRST_CUE_MIN_T} s")
    peaks = []
    if is_num(doc.get("drop")):
        peaks = [(doc["drop"], doc["drop_end"]) if is_num(doc.get("drop_end")) else doc["drop"]]
    viol = C.rate_violations(aerial_t, secs, peaks) if secs else []
    for t, n, cap in viol[:10]:
        err.append(f"{n} aerial cues in [{t:.3f}, {t + C.RATE_WINDOW:.3f}) (cap {cap})")
    if len(viol) > 10:
        err.append(f"... {len(viol) - 10} more rate violations")

    # same type, same spot, back to back (within 4 s)
    last: dict[str, dict] = {}
    repeats = 0
    for c in cues:
        p = last.get(c.get("type"))
        if p is not None and 0 < c["t"] - p["t"] < 4.0 and abs(c["x"] - p["x"]) < 10 and abs(c["z"] - p["z"]) < 8:
            repeats += 1
            if repeats <= 5:
                warn.append(f"same type '{c['type']}' from the same spot at t={p['t']} and t={c['t']}")
        last[c.get("type")] = c
    if repeats > 5:
        warn.append(f"... {repeats - 5} more same-spot repeats")

    if analysis is not None:
        grid = sorted(set(analysis["beats"]) | {h["t"] for h in analysis["hits"]})
        import bisect
        off = 0
        for c in cues:
            t = c["t"]
            i = bisect.bisect_left(grid, t)
            d = min(abs(t - grid[j]) for j in (i - 1, i) if 0 <= j < len(grid))
            if d > 0.010:
                off += 1
                if off <= 5:
                    err.append(f"t={t} is {d * 1000:.0f} ms from the nearest beat / strong onset")
        if off > 5:
            err.append(f"... {off - 5} more cues off the beat grid")

    # stats
    win = {}
    for s in secs:
        n = sum(1 for t in aerial_t if s["start"] <= t < s["end"])
        win[s["name"] + f"@{s['start']:.1f}"] = (n, n / max(s["end"] - s["start"], 1e-9))
    counts: dict[str, int] = {}
    for c in cues:
        counts[c.get("type")] = counts.get(c.get("type"), 0) + 1
    peak = 0
    ts = sorted(aerial_t)
    import bisect as _b
    for i, t in enumerate(ts):
        peak = max(peak, _b.bisect_left(ts, t + C.RATE_WINDOW - 1e-9) - i)
    load, load_t = C.load_curve_peak([c for c in cues if is_num(c.get("t")) and is_num(c.get("scale"))])
    if load > C.LOAD_CAP + 1e-6:
        err.append(f"particle load {load:.1f} at t={load_t:.2f} exceeds {C.LOAD_CAP:.0f} "
                   "(sum of scale over shells still alive)")
    stats = {"cues": len(cues), "aerial": len(aerial_t), "types": counts, "per_section": win,
             "peak_per_window": peak, "hits": sum(1 for c in cues if c.get("hit")), "peak_load": round(load, 1),
             "peak_load_t": round(load_t, 2)}
    return err, warn, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cues")
    ap.add_argument("--analysis", help="also check that every t sits on a beat or strong onset")
    ap.add_argument("--music-dir", help="warn if the music file is not in this folder")
    args = ap.parse_args()
    try:
        doc = json.loads(Path(args.cues).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read {args.cues}: {e}")
        return 1
    analysis = json.loads(Path(args.analysis).read_text()) if args.analysis else None
    err, warn, stats = validate(doc, analysis, Path(args.music_dir) if args.music_dir else None)
    for w in warn:
        print(f"WARN  {w}")
    for e in err:
        print(f"ERROR {e}")
    if stats:
        print(f"{stats['cues']} cues ({stats['aerial']} aerial, {stats['hits']} hit), "
              f"peak {stats['peak_per_window']} per {C.RATE_WINDOW:.0f} s window, "
              f"peak particle load {stats['peak_load']} / {C.LOAD_CAP:.0f} at {stats['peak_load_t']} s")
        print("types: " + ", ".join(f"{k} {v}" for k, v in sorted(stats["types"].items())))
        for k, (n, r) in stats["per_section"].items():
            print(f"  {k:<18} {n:4d} aerial  {r:5.2f}/s")
    print("FAIL" if err else "OK")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
