"""Single source of truth for the cue JSON contract (Docs/설계.md) and show rules.

choreograph.py builds against these values and validate_cues.py checks against them,
so the two can never drift apart.
"""
from __future__ import annotations

import bisect

VERSION = 1

TYPES = ["peony", "chrysanthemum", "willow", "ring", "crossette", "strobe", "palm", "comet", "fountain"]
AERIAL = [t for t in TYPES if t != "fountain"]
PALETTES = ["gold", "silver", "red", "orange", "green", "blue", "purple", "pink", "cyan", "mixed"]
SECTION_NAMES = ["intro", "verse", "build", "chorus", "bridge", "finale", "outro"]

X_RANGE = (-90.0, 90.0)
Z_RANGE = (-10.0, 30.0)
H_RANGE = (45.0, 120.0)  # aerial shells; fountain h == 0
SCALE_RANGE = (0.6, 1.6)

TOP_REQUIRED = ["version", "music", "credit", "duration", "bpm", "sections", "cues"]
TOP_OPTIONAL = ["generator", "seed", "style", "drop", "drop_end", "tempo_map"]  # Unity ignores these
CUE_REQUIRED = ["t", "type", "x", "z", "h", "palette", "scale"]
CUE_OPTIONAL = ["hit", "dur"]

# Show rules (from the lead's brief, not the Unity contract)
FIRST_CUE_MIN_T = 1.0
MAX_RATE = 6  # aerial cues in any 1 s window
FINALE_MAX_RATE = 12
RATE_WINDOW = 1.0
# A "peak window" (the slowburn drop eruption, given as (start, end)) may use the finale cap as well;
# a bare time t means (t, t + 1 s).

# Particle load: a shell counts as `scale` units while it is alive (seconds after its burst).
# 40 units is about 30 shells at scale 1.3 on screen at once.
LIFETIME = {"peony": 2.5, "chrysanthemum": 3.0, "willow": 5.0, "ring": 2.5, "crossette": 3.0, "strobe": 3.0,
            "palm": 3.5, "comet": 2.0}
LOAD_CAP = 40.0


def counts_toward_rate(cue: dict) -> bool:
    return cue.get("type") != "fountain"


def cap_at(t0: float, t1: float, sections: list[dict], peaks=()) -> int:
    """Rate cap for the window [t0, t1): the finale cap if the window touches the finale or the
    second right after it (the climax hit usually lands exactly on the finale's end boundary),
    or the second after a peak moment (the slowburn drop)."""
    for s in sections:
        if s["name"] == "finale" and s["start"] < t1 and t0 < s["end"] + RATE_WINDOW:
            return FINALE_MAX_RATE
    for p in peaks:
        p0, p1 = p if isinstance(p, (tuple, list)) else (p, p + RATE_WINDOW)
        if p0 < t1 and t0 < p1:
            return FINALE_MAX_RATE
    return MAX_RATE


def rate_violations(times: list[float], sections: list[dict], peaks=()) -> list[tuple[float, int, int]]:
    """Every window [t_i, t_i + 1 s) holding more aerial cues than its cap -> (t_i, count, cap)."""
    ts = sorted(times)
    out = []
    for i, t in enumerate(ts):
        j = bisect.bisect_left(ts, t + RATE_WINDOW - 1e-9)
        n = j - i
        cap = cap_at(t, t + RATE_WINDOW, sections, peaks)
        if n > cap:
            out.append((t, n, cap))
    return out


def would_fit(sorted_times: list[float], t: float, sections: list[dict], peaks=()) -> bool:
    """True if adding an aerial cue at t keeps every 1 s window within its cap."""
    lo = bisect.bisect_left(sorted_times, t - RATE_WINDOW + 1e-9)
    hi = bisect.bisect_right(sorted_times, t)
    starts = sorted_times[lo:hi] + [t]
    for s in starts:
        a = bisect.bisect_left(sorted_times, s)
        b = bisect.bisect_left(sorted_times, s + RATE_WINDOW - 1e-9)
        n = b - a + (1 if s <= t < s + RATE_WINDOW else 0)
        if n > cap_at(s, s + RATE_WINDOW, sections, peaks):
            return False
    return True


def load_curve_peak(cues: list[dict]) -> tuple[float, float]:
    """(peak particle load, time of the peak) for aerial cues."""
    ev = []
    for c in cues:
        if c.get("type") in LIFETIME:
            ev.append((c["t"], c["scale"]))
            ev.append((c["t"] + LIFETIME[c["type"]], -c["scale"]))
    ev.sort(key=lambda e: (e[0], e[1]))  # ends before starts at the same instant
    load = peak = 0.0
    when = 0.0
    for t, d in ev:
        load += d
        if load > peak:
            peak, when = load, t
    return peak, when


def load_fits(starts, ends, scales, t: float, typ: str, scale: float, cap: float = LOAD_CAP) -> bool:
    """True if a new shell (t, typ, scale) keeps the alive load <= cap over its whole life.
    starts/ends/scales: numpy arrays of the shells already accepted."""
    import numpy as np
    life = LIFETIME.get(typ)
    if life is None or len(starts) == 0:
        return life is None or scale <= cap
    pts = np.concatenate([[t], starts[(starts > t) & (starts < t + life)]])
    alive = (starts[None, :] <= pts[:, None]) & (ends[None, :] > pts[:, None])
    return bool(np.max(alive @ scales) + scale <= cap + 1e-9)
