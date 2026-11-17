#!/usr/bin/env python3
"""Turn analysis.json into the show's cue JSON (contract: Docs/설계.md).

    python3 Tools/beatmap/choreograph.py <analysis.json> -o <cues.json> --music <file> --credit "<text>" [--seed N]

Rules
  * density follows section energy; intro: a few comets + singles; build: comets speeding up
    and climbing; chorus / high energy: a hit + symmetric volley on every downbeat with fills;
    bridge / low energy: slow willows and palms; finale: accelerating barrage ending in a gold
    willow curtain on the climax hit; outro: a few lingering shells, one last single, silence.
  * positions follow patterns (left->right sweeps, centre-out, mirrored pairs); the same type is
    never fired twice in a row from the same spot.
  * every burst time is a beat or a strong onset; first cue >= 1 s; at most 6 aerial cues in any
    1 s window (12 in the finale).
  * deterministic for a given --seed.
  * --style slowburn [--drop S] [--climax S]: see class SlowBurn.
  * --labels intro,build,chorus,... overrides the automatic section names (one per section);
    --boundaries 16.3,32.3,... replaces the detected section starts (snapped to bars).
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract as C  # noqa: E402

H_BY_TYPE = {"peony": (78, 100), "chrysanthemum": (80, 104), "ring": (72, 96), "crossette": (66, 90),
             "strobe": (60, 84), "palm": (86, 106), "willow": (100, 116), "comet": (60, 110)}
CHORUS_COLORS = ["red", "gold", "blue", "green", "pink", "purple", "orange", "cyan"]
SNAP_TO_BEAT = 0.08  # a strong onset this close to a beat is the beat


@dataclass
class Cue:
    t: float
    type: str
    x: float
    z: float
    h: float
    palette: str
    scale: float
    prio: int
    hit: bool = False
    dur: float | None = None
    mirror: int | None = None  # id of the mirrored partner (same t, -x)
    id: int = field(default=0)

    def out(self) -> dict:
        d = {"t": round(self.t, 3), "type": self.type, "x": round(self.x, 1), "z": round(self.z, 1),
             "h": round(self.h, 1), "palette": self.palette, "scale": round(self.scale, 2)}
        if self.hit:
            d["hit"] = True
        if self.dur is not None:
            d["dur"] = round(self.dur, 2)
        return d


# --------------------------------------------------------------------------- section names

def label_sections(segs: list[dict], duration: float) -> list[str]:
    n = len(segs)
    E = [s["energy"] for s in segs]
    if n == 1:
        return ["chorus"]
    names: list[str | None] = [None] * n
    hi = max(0.6, max(E) - 0.25)
    lo = 0.4
    if E[-1] < E[-2] - 0.12 or segs[-1]["slope_db_per_s"] < -1.0 or E[-1] < lo:
        names[-1] = "outro"
    if E[0] < hi or segs[0]["end"] - segs[0]["start"] < 20.0:
        names[0] = "intro"
    free = [i for i in range(n) if names[i] is None]
    if free:
        top = max(E[i] for i in free)
        late = [i for i in free if E[i] >= top - 0.12 and segs[i]["end"] > 0.6 * duration]
        names[max(late) if late else max(free, key=lambda i: (E[i], i))] = "finale"
    for i in range(n):
        if names[i] is not None:
            continue
        nxt = E[i + 1] if i + 1 < n else None
        rising = segs[i]["slope_db_per_s"] > 0.15
        if nxt is not None and nxt >= E[i] + 0.15 and nxt >= hi and (rising or lo <= E[i] < hi):
            names[i] = "build"  # leads into something clearly bigger and climbs (or sits mid-level)
        elif E[i] >= hi:
            names[i] = "chorus"
        elif E[i] < lo:
            names[i] = "bridge"
        else:
            names[i] = "verse"
    return names  # type: ignore[return-value]


def resegment(a: dict, boundaries: list[float]) -> list[dict]:
    """Hand-picked section starts (seconds), each snapped to the nearest bar start."""
    from analyze import make_segments
    bars = a["bars"]
    starts = np.array([b["t"] for b in bars])
    idx = sorted({int(np.argmin(np.abs(starts - t))) for t in boundaries} - {0})
    return make_segments(bars, [0] + idx + [len(bars)], float(a["duration"]))


# --------------------------------------------------------------------------- show builder

class Show:
    def __init__(self, a: dict, seed: int, labels: list[str] | None, boundaries: list[float] | None = None):
        self.a = a
        self.rng = np.random.default_rng(seed)
        self.duration = float(a["duration"])
        self.beats = np.array(a["beats"], dtype=float)
        self.downbeats = np.array(a["downbeats"], dtype=float)
        self.period = float(np.median(np.diff(self.beats)))
        self.bar = 4 * self.period
        self.hits = [(float(h["t"]), float(h["strength"])) for h in a["hits"]]
        # snap grid: beats + strong onsets that are not already a beat
        extra = [t for t, s in self.hits if s >= 0.5 and np.min(np.abs(self.beats - t)) > SNAP_TO_BEAT]
        self.grid = np.array(sorted(set(self.beats.tolist()) | set(extra)))
        segs = a["segments"]
        if boundaries:
            segs = resegment(a, boundaries)
        names = labels or label_sections(segs, self.duration)
        if len(names) != len(segs):
            raise SystemExit(f"--labels has {len(names)} names but the analysis has {len(segs)} sections")
        bad = [n for n in names if n not in C.SECTION_NAMES]
        if bad:
            raise SystemExit(f"unknown section names: {bad}")
        self.sections = [{"name": nm, "start": float(s["start"]), "end": float(s["end"]),
                          "energy": float(s["energy"])} for nm, s in zip(names, segs)]
        self.sections[0]["start"] = 0.0
        self.sections[-1]["end"] = self.duration
        self.segs = segs
        self.cues: list[Cue] = []
        self._next_id = 1
        self._count: dict[str, int] = {}
        self.style = "default"
        self.peaks: list[float] = []  # moments that may use the finale rate cap (slowburn drop)
        self.drop: float | None = None
        self.tempo_segments = a.get("tempo_segments") or []
        self.multi_tempo = len(self.tempo_segments) > 1
        self._global_period = self.period

    # ----- helpers
    def local_period(self, t: float) -> float:
        """Median beat interval around t (songs with tempo changes)."""
        i = int(np.searchsorted(self.beats, t))
        d = np.diff(self.beats[max(0, i - 4) : i + 4])
        return float(np.median(d)) if len(d) else self._global_period

    def use_tempo_of(self, a: float, b: float) -> None:
        """Make self.period / self.bar describe [a, b) (only when the song changes tempo)."""
        if not self.multi_tempo:
            return
        bt = self.beats_in(a, b)
        self.period = float(np.median(np.diff(bt))) if len(bt) > 2 else self.local_period((a + b) / 2)
        self.bar = 4 * self.period

    def snap(self, t: float) -> float:
        return float(self.grid[int(np.argmin(np.abs(self.grid - t)))])

    def beats_in(self, a: float, b: float) -> np.ndarray:
        return self.beats[(self.beats >= a - 1e-6) & (self.beats < b - 1e-6)]

    def downbeats_in(self, a: float, b: float) -> np.ndarray:
        return self.downbeats[(self.downbeats >= a - 1e-6) & (self.downbeats < b - 1e-6)]

    def is_downbeat(self, t: float) -> bool:
        return bool(len(self.downbeats)) and float(np.min(np.abs(self.downbeats - t))) < 1e-3

    def nth(self, key: str) -> int:
        self._count[key] = self._count.get(key, 0) + 1
        return self._count[key] - 1

    def j(self, s: float) -> float:
        return float(self.rng.uniform(-s, s))

    def height(self, typ: str, frac: float | None = None) -> float:
        lo, hi = H_BY_TYPE[typ]
        f = self.rng.uniform() if frac is None else frac
        return lo + (hi - lo) * f

    def add(self, t, typ, x, z, palette, scale, prio, h=None, hit=False, dur=None) -> Cue:
        if t < C.FIRST_CUE_MIN_T - 1e-6 or t > self.duration - 0.25:
            return None  # type: ignore[return-value]
        if h is None:
            h = 0.0 if typ == "fountain" else self.height(typ)
        c = Cue(t=float(t), type=typ, x=float(x), z=float(z), h=float(h),
                palette=palette, scale=float(scale), prio=prio, hit=hit, dur=dur, id=self._next_id)
        if typ == "fountain":
            c.h = 0.0
        self._next_id += 1
        self.cues.append(c)
        return c

    def pair(self, t, typ, x, z, palette, scale, prio, h=None) -> None:
        """Mirrored pair at +-x (same height, scale, colour)."""
        h = h if h is not None else self.height(typ)
        jx = self.j(3)
        a = self.add(t, typ, -abs(x) - jx, z, palette, scale, prio, h)
        b = self.add(t, typ, abs(x) + jx, z, palette, scale, prio, h)
        if a and b:
            a.mirror, b.mirror = b.id, a.id

    @staticmethod
    def sweep(n: int, direction: int, span: float = 72.0) -> list[float]:
        xs = np.linspace(-span, span, n) if n > 1 else np.array([0.0])
        return list(xs[::direction])

    @staticmethod
    def row(kind: str) -> float:
        return {"front": -3.0, "mid": 10.0, "back": 22.0}[kind]

    # ----- section generators
    def gen_intro(self, s: dict) -> None:
        a = max(s["start"], C.FIRST_CUE_MIN_T)
        dbs = self.downbeats_in(a, s["end"] - 0.5 * self.bar)
        if len(dbs) == 0:
            dbs = self.beats_in(a, s["end"])[::4]
        last = -1e9
        xs = [0.0, -32.0, 32.0, -55.0, 55.0, -16.0, 16.0]
        k = 0
        for t in dbs:
            p = (t - s["start"]) / max(s["end"] - s["start"], 1e-6)
            gap = max(self.bar, 3.5) if p < 0.5 else max(self.bar, 1.8)  # sparse, then every bar
            if t - last < gap * 0.95:
                continue
            x = xs[k % len(xs)]
            if k % 3 != 2:
                self.add(t, "comet", x + self.j(2), self.row("mid") + self.j(3), "silver" if k % 2 == 0 else "cyan",
                         0.9, 80, h=70 + 30 * p)
            else:
                typ = ["peony", "ring"][(k // 3) % 2]
                self.add(t, typ, x + self.j(2), self.row("back") + self.j(3), ["blue", "silver", "cyan"][k % 3],
                         0.8 + 0.1 * p, 80)
            last, k = t, k + 1

    def gen_build(self, s: dict) -> None:
        """Comets on a grid that halves as the build goes on (bar -> half bar -> beat), each one
        higher than the last; small strobe pairs on the last downbeats; twin comets on the final beat."""
        a = max(s["start"], C.FIRST_CUE_MIN_T)
        beats = self.beats_in(s["start"], s["end"])
        if len(beats) == 0:
            return
        L = max(s["end"] - s["start"], 1e-6)
        dbs = self.downbeats_in(s["start"], s["end"])
        ref = float(dbs[0]) if len(dbs) else float(beats[0])
        i_ref = int(np.argmin(np.abs(beats - ref)))
        zig = [-60.0, -30.0, 0.0, 30.0, 60.0, 30.0, 0.0, -30.0]
        k = 0
        for i, t in enumerate(beats[:-1]):
            if t < a:
                continue
            p = (t - s["start"]) / L
            step = 4 if p < 0.5 else 2 if p < 0.75 else 1  # every bar -> every half bar -> every beat
            if (i - i_ref) % step:
                continue
            pal = "silver" if p < 0.5 else "gold"
            self.add(t, "comet", zig[k % len(zig)] + self.j(3), self.row("mid") + self.j(4), pal, 0.9 + 0.3 * p,
                     80, h=58 + 55 * p)
            if p > 0.7 and self.is_downbeat(t):
                self.pair(t, "strobe", 74 - 16 * p, self.row("front"), "silver", 0.7, 50, h=62 + 10 * p)
            k += 1
        self.pair(float(beats[-1]), "comet", 50, self.row("back"), "gold", 1.2, 85, h=116)

    def volley(self, t: float, n_extra: int, types: tuple[str, str], palettes: tuple[str, str], prio: int,
               spread_idx: int, scale: float = 1.2) -> None:
        """Centre shell (hit) + mirrored pairs spreading outwards."""
        self.add(t, types[0], self.j(2), self.row("back") + self.j(3), palettes[0], scale + self.j(0.05), prio + 5,
                 h=self.height(types[0], 0.85), hit=True)
        spreads = [[34, 60], [52, 74], [24, 46], [44, 68]][spread_idx % 4]
        for i in range(n_extra):
            x = spreads[i % 2] + 12 * (i // 2)
            self.pair(t, types[1], min(x, 84), self.row("mid") + self.j(3), palettes[1], scale - 0.15 + self.j(0.05),
                      prio, h=self.height(types[1], 0.55))

    def gen_verse(self, s: dict) -> None:
        a = max(s["start"], C.FIRST_CUE_MIN_T)
        beats = self.beats_in(a, s["end"])
        rate = 0.6 + 1.2 * s["energy"]
        acc = 0.0
        types = ["peony", "ring", "crossette", "strobe", "chrysanthemum"]
        pals = ["blue", "green", "cyan", "purple", "silver"]
        direction = 1
        xs = self.sweep(5, direction)
        k = 0
        for i, t in enumerate(beats):
            acc += rate * self.period
            dn = self.is_downbeat(t)
            if dn and self.nth("verse_bar") % 4 == 0:
                c = self.nth("verse_pair")
                self.pair(t, types[c % 5], 45, self.row("back"), pals[c % 5], 1.0, 70)
                acc -= 2
                continue
            if acc >= 1 and (dn or i % 2 == 0 or acc >= 2):
                if k % len(xs) == 0 and k:
                    direction = -direction
                    xs = self.sweep(5, direction)
                typ = types[(k + 1) % 5]
                self.add(t, typ, xs[k % len(xs)] + self.j(4), self.row("mid") + self.j(5), pals[k % 5],
                         0.85 + 0.25 * s["energy"], 60 if dn else 45)
                acc -= 1
                k += 1

    def gen_chorus(self, s: dict, finale_like: bool = False) -> None:
        a = max(s["start"], C.FIRST_CUE_MIN_T)
        beats = self.beats_in(a, s["end"])
        e = s["energy"]
        occ = self.nth("chorus")
        fill_rate = max(0.0, 1.5 + 3.5 * e - 4.0 / max(self.bar, 1e-6))
        acc = 0.0
        bar_i = 0
        fills_in_bar: list[float] = []
        direction = 1
        vol_types = [("peony", "chrysanthemum"), ("chrysanthemum", "ring"), ("ring", "peony"),
                     ("peony", "ring"), ("chrysanthemum", "peony")]
        fill_types = ["crossette", "strobe", "peony", "ring", "chrysanthemum"]

        def flush(bar_beats: list[float]) -> None:
            nonlocal direction
            xs = self.sweep(max(len(bar_beats), 2), direction, [66, 52, 74, 44][bar_i % 4])
            for m, tb in enumerate(bar_beats):
                typ = fill_types[(m + bar_i + occ) % 5]
                self.add(tb, typ, xs[m] + self.j(4), self.row("front") + self.j(4),
                         CHORUS_COLORS[(bar_i + m + 2 + occ) % 8], 0.8 + 0.15 * e, 40)
            direction = -direction

        for t in beats:
            if self.is_downbeat(t):
                flush(fills_in_bar)
                fills_in_bar = []
                vt = vol_types[(bar_i + occ) % 5]
                c1 = CHORUS_COLORS[(bar_i + occ) % 8]
                c2 = CHORUS_COLORS[(bar_i + occ + 3) % 8]
                strong = any(abs(ht - t) < SNAP_TO_BEAT and hs >= 0.6 for ht, hs in self.hits)
                self.volley(t, 2 if (strong or bar_i % 2 == 0) else 1, vt, (c1, c2), 85, bar_i, scale=1.15 + 0.2 * e)
                bar_i += 1
                continue
            acc += fill_rate * self.period
            while acc >= 1:
                fills_in_bar.append(float(t))
                acc -= 1
        flush(fills_in_bar)
        # accent pops on strong off-grid onsets
        for ht, hs in self.hits:
            if a <= ht < s["end"] and hs >= 0.7 and np.min(np.abs(self.beats - ht)) > SNAP_TO_BEAT:
                k = self.nth("accent")
                self.add(ht, "ring", [-50, 50, -20, 20][k % 4], self.row("mid"), CHORUS_COLORS[k % 8], 0.8, 50)

    def gen_bridge(self, s: dict) -> None:
        a = max(s["start"], C.FIRST_CUE_MIN_T)
        dbs = self.downbeats_in(a, s["end"])
        L = s["end"] - s["start"]
        if L >= 6.0 and len(dbs) >= 2:
            t0 = float(dbs[0])
            t1 = float(dbs[-1]) if len(dbs) > 2 else s["end"] - self.period
            for x, pal in ((-35.0, "gold"), (35.0, "gold")):
                self.add(t0, "fountain", x, -6.0, pal, 1.0, 70, dur=max(1.0, t1 - t0))
        gap = max(self.bar, 2.0)
        last = -1e9
        k = 0
        xs = [0.0, -42.0, 42.0, -20.0, 20.0, -62.0, 62.0]
        palm_pal = ["gold", "orange", "green", "purple"]
        cands = self.beats_in(a, s["end"] - 0.5 * self.period)
        for t in cands:
            if not self.is_downbeat(t) and t - last < 2 * gap:
                continue
            if t - last < gap * 0.95:
                continue
            if k % 2 == 0:
                self.add(t, "willow", xs[k % len(xs)] + self.j(3), self.row("back") + self.j(3),
                         "gold" if k % 4 == 0 else "silver", 1.1 + 0.2 * s["energy"], 80)
            elif k % 6 == 3:
                self.pair(t, "palm", 40, self.row("mid"), palm_pal[k % 4], 1.0, 80)
            else:
                self.add(t, "palm", xs[k % len(xs)] + self.j(3), self.row("mid") + self.j(3), palm_pal[k % 4],
                         1.0 + 0.2 * s["energy"], 80)
            last, k = t, k + 1

    def gen_finale(self, s: dict, climax: float) -> None:
        a = max(s["start"], C.FIRST_CUE_MIN_T)
        dbs = self.downbeats_in(a, s["end"])
        if s["end"] - s["start"] >= 6.0 and len(dbs):
            t0 = float(dbs[0])
            t1 = min(climax + 2 * self.period, self.duration - 0.5)
            for i, x in enumerate((-66.0, -22.0, 22.0, 66.0)):
                self.add(t0, "fountain", x, -6.0, ["gold", "silver"][i % 2], 1.1, 70, dur=max(1.0, t1 - t0))
        beats = self.beats_in(a, climax - 1e-3)
        if len(beats):
            L = max(climax - a, 1e-6)
            acc = 0.0
            mix = ["chrysanthemum", "peony", "ring", "crossette", "strobe", "palm", "peony", "chrysanthemum"]
            direction = 1
            bar_i = 0
            for i, t in enumerate(beats):
                p = (t - a) / L
                rate = 4.0 + 7.5 * p ** 1.3
                acc += rate * self.period
                n = int(acc)
                if n <= 0:
                    continue
                acc -= n
                if self.is_downbeat(t):
                    vt = (mix[bar_i % 8], mix[(bar_i + 3) % 8])
                    self.volley(t, max(1, (n - 1) // 2), vt, ("mixed", CHORUS_COLORS[bar_i % 8]), 85, bar_i,
                                scale=1.3)
                    bar_i += 1
                else:
                    span = [80, 62, 72, 50][i % 4]
                    xs = self.sweep(max(n, 2), direction, span)[:n] if n > 1 else [direction * span * 0.6]
                    for m, x in enumerate(xs):
                        typ = mix[(i + m) % 8]
                        self.add(t, typ, x + self.j(4), self.row("front" if m % 2 else "mid") + self.j(4),
                                 CHORUS_COLORS[(i + m) % 8], 1.0 + 0.3 * p, 60)
                    direction = -direction
        self.curtain(climax)

    def curtain(self, t: float) -> None:
        """Gold willows across the whole sky on the climax, a second layer one second later."""
        for i, x in enumerate(np.linspace(-80, 80, 9)):
            self.add(t, "willow", x, 2.0 if i % 2 else 18.0, "gold", 1.6 if i % 2 == 0 else 1.5, 100,
                     h=118 if i % 2 == 0 else 108, hit=(i == 4))
        later = self.beats[self.beats >= t + C.RATE_WINDOW - 1e-6]
        if len(later) and later[0] < t + 2.0:
            for x in (-60.0, -30.0, 30.0, 60.0):
                self.add(float(later[0]), "willow", x, 26.0, "gold", 1.4, 95, h=113)

    def gen_after_climax(self, climax: float) -> None:
        """After the curtain: let it fall, a few lingering shells if there is room,
        one last single, then at least 3.5 s of silence before the song ends."""
        quiet_from = self.duration - 3.5
        cands = self.downbeats_in(climax + 2.5, quiet_from + 1e-3)
        if len(cands) == 0:
            cands = self.beats_in(climax + 2.5, quiet_from + 1e-3)
        if len(cands) == 0:
            return
        t_last = float(cands[-1])
        last = climax + 0.5  # curtain + its second layer
        k = 0
        for t in self.downbeats_in(climax + 4.0, t_last - 3.0 + 1e-3):
            if t - last < 3.5:
                continue
            typ = ["willow", "comet"][k % 2]
            self.add(float(t), typ, [-40.0, 40.0, -15.0, 15.0][k % 4], self.row("back"), "silver", 1.0, 80,
                     h=110 if typ == "willow" else 100)
            last, k = float(t), k + 1
        self.add(t_last, "chrysanthemum", 0.0, 12.0, "gold", 1.3, 99, h=112)

    # ----- climax
    def find_climax(self) -> tuple[float, int]:
        idx = [i for i, s in enumerate(self.sections) if s["name"] == "finale"]
        if not idx:
            body = [i for i, s in enumerate(self.sections) if s["name"] != "outro"] or [len(self.sections) - 1]
            idx = [max(body, key=lambda i: (self.sections[i]["energy"], i))]
        fi = idx[-1]
        f = self.sections[fi]
        lo = f["start"] + 0.5 * (f["end"] - f["start"])
        hi = min(f["end"] + self.bar, self.duration - 2.0)
        best, best_score = None, -1.0
        for ht, hs in self.hits:
            if lo <= ht <= hi:
                score = hs * (1.25 if np.min(np.abs(self.downbeats - ht)) < SNAP_TO_BEAT else 1.0)
                score *= 1.0 + 0.3 * (ht - lo) / max(hi - lo, 1e-6)
                if score > best_score:
                    best, best_score = ht, score
        if best is None:
            dbs = self.downbeats_in(lo, hi + 1e-3)
            best = float(dbs[-1]) if len(dbs) else min(f["end"], self.duration - 3.0)
        return self.snap(best), fi

    # ----- passes
    def build(self) -> None:
        climax, fi = self.find_climax()
        self.climax = climax
        for i, s in enumerate(self.sections):
            if s["start"] >= climax - 1e-6:
                continue  # handled by gen_after_climax
            s = dict(s, end=min(s["end"], climax))  # nothing but the curtain at/after the climax
            name = s["name"]
            self.use_tempo_of(s["start"], s["end"])
            if i == fi or name == "finale":
                self.gen_finale(s, climax) if i == fi else self.gen_chorus(s)
            elif name == "intro":
                self.gen_intro(s)
            elif name == "build":
                self.gen_build(s)
            elif name == "chorus":
                self.gen_chorus(s)
            elif name in ("bridge", "outro"):
                self.gen_bridge(s)
            else:
                self.gen_verse(s)
        if fi is not None and self.sections[fi]["start"] >= climax - 1e-6:
            self.curtain(climax)  # finale started at/after the climax hit (unusual): still crown it
        self.use_tempo_of(climax, self.duration)
        self.gen_after_climax(climax)
        self.limit_rate()
        self.fix_repeats()

    def limit_rate(self) -> None:
        """Greedy by priority: keep a cue (or a mirrored pair, all or nothing) only if every
        1 s window stays within its cap."""
        by_id = {c.id: c for c in self.cues}
        units, seen = [], set()
        for c in self.cues:
            if c.id in seen:
                continue
            unit = [c] + ([by_id[c.mirror]] if c.mirror in by_id else [])
            seen.update(u.id for u in unit)
            units.append(unit)
        units.sort(key=lambda u: (-u[0].prio, u[0].t, u[0].id))
        keep: list[Cue] = []
        times: list[float] = []
        starts: list[float] = []
        ends: list[float] = []
        scales: list[float] = []
        for unit in units:
            if not C.counts_toward_rate({"type": unit[0].type}):
                keep.extend(unit)
                continue
            trial = list(times)
            st, en, sc = np.array(starts), np.array(ends), np.array(scales)
            ok = True
            for c in unit:
                if not C.would_fit(trial, c.t, self.sections, self.peaks) or \
                        not C.load_fits(st, en, sc, c.t, c.type, c.scale):
                    ok = False
                    break
                bisect.insort(trial, c.t)
                st = np.append(st, c.t)
                en = np.append(en, c.t + C.LIFETIME[c.type])
                sc = np.append(sc, c.scale)
            if ok:
                times = trial
                keep.extend(unit)
                starts, ends, scales = st.tolist(), en.tolist(), sc.tolist()
        kept = {c.id for c in keep}
        for c in keep:
            if c.mirror is not None and c.mirror not in kept:
                c.mirror = None
        self.cues = sorted(keep, key=lambda c: (c.t, c.x))

    def fix_repeats(self) -> None:
        """No back-to-back same type from the same spot: move the later one (and its mirror)."""
        by_id = {c.id: c for c in self.cues}
        last: dict[str, Cue] = {}
        for c in self.cues:
            p = last.get(c.type)
            if p is not None and p.t < c.t and c.t - p.t < 4.0 and abs(c.x - p.x) < 10 and abs(c.z - p.z) < 8:
                if abs(c.x) < 8:  # centre shell: change depth row instead
                    c.z = p.z + 14 if p.z < 12 else p.z - 14
                else:
                    shift = 22.0 if c.x < p.x or c.x < -60 else -22.0
                    shift = -shift if abs(c.x + shift) > 86 else shift
                    c.x += shift
                    m = by_id.get(c.mirror) if c.mirror else None
                    if m is not None:
                        m.x = -c.x
            last[c.type] = c

    def to_json(self, music: str, credit: str, seed: int) -> dict:
        cues = []
        for c in sorted(self.cues, key=lambda c: (c.t, c.x)):
            c.x = float(np.clip(c.x, -88, 88))
            c.z = float(np.clip(c.z, -8, 28))
            if c.type != "fountain":
                c.h = float(np.clip(c.h, C.H_RANGE[0] + 1, C.H_RANGE[1] - 1))
            c.scale = float(np.clip(c.scale, *C.SCALE_RANGE))
            if c.dur is not None:
                c.dur = min(c.dur, self.duration - c.t)
            cues.append(c.out())
        return {
            "version": C.VERSION,
            "music": music,
            "credit": credit,
            "duration": round(self.duration, 3),
            "bpm": round(float(self.a["bpm"]), 2),
            "sections": [{"name": s["name"], "start": round(s["start"], 3), "end": round(s["end"], 3),
                          "energy": round(s["energy"], 3)} for s in self.sections],
            "cues": cues,
            "generator": "Tools/beatmap/choreograph.py",
            "seed": seed,
            **({"style": self.style} if self.style != "default" else {}),
            **({"drop": round(self.drop, 3)} if self.drop is not None else {}),
            **({"drop_end": round(self.peaks[0][1], 3)} if self.peaks and isinstance(self.peaks[0], tuple) else {}),
            **({"tempo_map": [{"start": g["start"], "end": g["end"], "bpm": g["bpm"]} for g in self.tempo_segments]}
               if self.multi_tempo else {}),
        }


# --------------------------------------------------------------------------- slow-burn style

# Post-drop stages: each one leads with different shell types, colours and launch patterns.
THEMES = [
    {"types": ("ring", "crossette"), "salvo": ("ring", "crossette"), "pals": ("cyan", "blue", "silver")},
    {"types": ("peony", "strobe"), "salvo": ("peony", "peony"), "pals": ("red", "gold", "orange")},
    {"types": ("chrysanthemum", "crossette"), "salvo": ("chrysanthemum", "ring"), "pals": ("green", "purple", "silver")},
    {"types": ("palm", "strobe"), "salvo": ("palm", "chrysanthemum"), "pals": ("orange", "gold", "pink")},
    {"types": ("crossette", "ring", "strobe"), "salvo": ("crossette", "peony"), "pals": ("pink", "purple", "cyan")},
    {"types": ("peony", "chrysanthemum", "palm"), "salvo": ("chrysanthemum", "peony"), "pals": ("blue", "gold", "red")},
]
FINALE_THEME = {"types": ("chrysanthemum", "peony", "crossette", "ring", "palm", "strobe"),
                "salvo": ("chrysanthemum", "peony"), "pals": ("mixed", "gold", "red", "blue", "green", "pink")}
PATTERNS = ("sweep", "callresp", "fan", "pulse", "chain")
STAGE_MIN, STAGE_TARGET, STAGE_MAX = 20.0, 30.0, 40.0  # solo stage length (s), 2026-11-17 lead: 20-40 s
STAGE_FLOOR, STAGE_STEP = 4.4, 0.4  # first stage >= 4 shells/s, then up a step each stage (cap 6)


class SlowBurn(Show):
    """'Start the engine slowly, then explode at the highlight' (Kingsman church scene feel).

    before the drop : very sparse - comets, single willows, small rings; a little more each chorus;
                      the last bars before the drop: comets speed up and climb; then 1-2 empty beats
    drop            : ~2 bars of eruption at 8-10 shells/s (full-width salvo + hit, symmetric big
                      volleys out to both edges, gold willow curtain on the last beat)
    after the drop  : 20-40 s stages, the first at >= 4 shells/s, each a step denser, changing shell types, colours and
                      launch patterns (no pattern twice in a row, one breathing bar per stage)
    final hit       : gold willow curtain, a few lingering shells, one last single, silence
    """

    def __init__(self, a: dict, seed: int, labels=None, boundaries=None, drop: float | None = None,
                 climax: float | None = None):
        super().__init__(a, seed, None, boundaries)
        self.style = "slowburn"
        self.user_labels = labels
        self.drop_arg = drop
        self.climax_arg = climax

    # ----- timing decisions
    def _rms_mean(self, a: float, b: float) -> float:
        hop = float(self.a["curve_hop"])
        r = np.array(self.a["rms_curve"])
        i0, i1 = max(0, int(a / hop)), max(int(a / hop) + 1, int(b / hop))
        return float(r[i0:i1].mean()) if i1 > i0 and i0 < len(r) else 0.0

    def _snap_downbeat(self, t: float) -> float:
        P = self.local_period(t)
        if len(self.downbeats):
            db = float(self.downbeats[int(np.argmin(np.abs(self.downbeats - t)))])
            if abs(db - t) <= 1.01 * P:
                return db
        return self.snap(t)

    def find_drop(self) -> float:
        if self.drop_arg is not None:
            return self._snap_downbeat(float(self.drop_arg))
        D = self.duration
        cands = [float(g["start"]) for g in self.tempo_segments[1:]]  # a tempo change is the drop
        if not cands:
            cands = [float(s["start"]) for s in self.segs[1:] if 0.12 * D < s["start"] < 0.88 * D]
        if not cands:
            cands = [0.4 * D]
        best = max(cands, key=lambda t: self._rms_mean(t, t + 20.0) - self._rms_mean(t - 20.0, t))
        # the drop is the first hard hit of the new part: if nothing lands on the change point itself
        # (a held vocal or a fill often bridges the two tempi), move to the first strong impact
        # within the next 4 s
        if self.drop_arg is None and not any(abs(ht - best) <= 0.1 and hs >= 0.4 for ht, hs in self.hits):
            nxt = sorted((ht, hs) for ht, hs in self.hits if best < ht <= best + 4.0 and hs >= 0.5)
            if nxt:
                self.drop_moved_from = best
                return self.snap(nxt[0][0])
        return self._snap_downbeat(best)

    drop_moved_from: float | None = None

    def _level_db(self, a: float, b: float) -> float:
        """Mean level in dB re the song's loud reference (rms_curve covers 40 dB)."""
        return 40.0 * self._rms_mean(a, b) - 40.0

    def fade_start(self, after: float) -> float | None:
        """Start of a fade-out ending (radio edits): the last moment still within 1 dB of the
        closing plateau, when the level then sinks >= 10 dB before the music ends."""
        end = float(self.a.get("music_end", self.duration))
        if end - after < 30.0:
            return None
        plateau = float(np.median([self._level_db(t, t + 1.0) for t in np.arange(end - 60.0, end - 20.0, 1.0)
                                   if t >= after]) if end - 60.0 >= after else self._level_db(after, end - 20.0))
        ts = np.arange(max(after, end - 40.0), end, 0.25)
        lv = np.array([self._level_db(t, t + 1.0) for t in ts])
        loud = np.nonzero(lv >= plateau - 1.0)[0]
        if len(loud) == 0:
            return None
        t_f = float(ts[loud[-1]] + 1.0)
        if plateau - lv[-1] >= 10.0 and end - t_f <= 25.0:
            return t_f
        return None

    def find_final_hit(self, drop: float) -> float:
        if self.climax_arg is not None:
            return self.snap(float(self.climax_arg))
        end = min(float(self.a.get("music_end", self.duration)), self.duration - 1.5)
        lo = max(drop + 0.6 * (end - drop), end - 45.0)
        fade = self.fade_start(drop)
        self.fade = fade
        if fade is not None:  # crown the last full-level moment, not something inside the fade
            end = min(end, fade + 0.5)
            lo = max(drop, fade - 2 * 4 * self.local_period(fade))
        best, best_score = None, -1.0
        for ht, hs in self.hits:
            if lo <= ht <= end:
                sc = hs * (1.25 if np.min(np.abs(self.downbeats - ht)) < SNAP_TO_BEAT else 1.0)
                sc *= 1.0 + 0.6 * (ht - lo) / max(end - lo, 1e-6)
                if sc > best_score:
                    best, best_score = ht, sc
        if best is None:
            dbs = self.downbeats_in(lo, end)
            best = float(dbs[-1]) if len(dbs) else end
        return self.snap(best)

    fade: float | None = None

    def plan_sections(self, drop: float, climax: float) -> list[dict]:
        D = self.duration
        pre = []
        for sg in self.segs:
            a, b = float(sg["start"]), min(float(sg["end"]), drop)
            if b - a > 1e-3:
                pre.append({"start": a, "end": b, "music": float(sg["energy"])})
        merged = []
        for p in pre:  # fold slivers (< 8 s) into the section before them
            if merged and p["end"] - p["start"] < 8.0:
                merged[-1]["end"] = p["end"]
            else:
                merged.append(p)
        if len(merged) > 1 and merged[0]["end"] - merged[0]["start"] < 8.0:
            merged[1]["start"] = merged[0]["start"]
            merged.pop(0)
        pre = merged
        for i in range(1, len(pre)):
            pre[i]["start"] = pre[i - 1]["end"]
        if pre:
            pre[0]["start"] = 0.0
            pre[-1]["end"] = drop
        # build = the last stretch before the drop (its own section if the analysis did not give one)
        P = self.local_period(drop - 1.0)
        build_len = float(np.clip(16 * P, 12.0, 24.0))
        if pre and (pre[-1]["end"] - pre[-1]["start"] > 40.0 or len(pre) == 1):
            cut = self._snap_downbeat(drop - build_len)
            if pre[-1]["start"] + 4.0 < cut < drop - 4.0:
                last = pre.pop()
                pre += [dict(last, end=cut), dict(last, start=cut)]
        names = []
        if pre:
            es = [p["music"] for p in pre[1:-1]]
            mid = (min(es) + max(es)) / 2 if es else 0.5
            contrast = len(es) > 1 and max(es) - min(es) >= 0.06  # louder parts really stand out
            for i, p in enumerate(pre):
                names.append("intro" if i == 0 and len(pre) > 1 else "build" if i == len(pre) - 1
                             else "chorus" if contrast and p["music"] >= mid else "verse")
        sections = []
        for nm, p in zip(names, pre):
            e = 0.55 if nm == "build" else min(0.4, 0.12 + 0.3 * p["music"])  # low bloom before the drop
            sections.append({"name": nm, "start": p["start"], "end": p["end"], "energy": e, "music": p["music"]})
        # stages after the drop
        cands = sorted(float(sg["start"]) for sg in self.segs if drop + STAGE_MIN < sg["start"] < climax - STAGE_MIN)
        cuts, t = [], drop
        while climax - t > STAGE_MAX:
            rem = climax - t
            target = t + rem / max(1, round(rem / STAGE_TARGET))
            near = [c for c in cands if t + STAGE_MIN <= c <= t + STAGE_MAX and abs(c - target) <= 0.3 * STAGE_TARGET]
            c = min(near, key=lambda c: abs(c - target)) if near else self._snap_downbeat(target)
            if c <= t + 1.0:
                break
            cuts.append(c)
            t = c
        if cuts and climax - cuts[-1] < STAGE_MIN and climax - (cuts[-2] if len(cuts) > 1 else drop) <= 1.25 * STAGE_MAX:
            cuts.pop()
        edges = [drop] + cuts + [climax]
        n = len(edges) - 1
        for i in range(n):
            final = i == n - 1
            sections.append({"name": "finale" if final else "chorus", "start": edges[i], "end": edges[i + 1],
                             "energy": 1.0 if final else 0.7 + 0.25 * i / max(1, n - 1), "stage": i,
                             "stages": n})
        if D - climax >= 1.0:
            sections.append({"name": "outro", "start": climax, "end": D,
                             "energy": min(0.45, self._rms_mean(climax, D) * 0.5 + 0.1)})
        else:
            sections[-1]["end"] = D
        sections[0]["start"] = 0.0
        if self.user_labels:
            if len(self.user_labels) != len(sections):
                raise SystemExit(f"--labels has {len(self.user_labels)} names but the slowburn plan has "
                                 f"{len(sections)} sections: {[x['name'] for x in sections]}")
            for x, nm in zip(sections, self.user_labels):
                x["name"] = nm
        return sections

    # ----- before the drop
    def gen_pre(self, s: dict, k_chorus: int) -> None:
        a, b = max(s["start"], C.FIRST_CUE_MIN_T), s["end"]
        name = s["name"]
        gap = {"intro": 8.0, "verse": 6.0}.get(name, max(2.4, 4.0 - 0.8 * k_chorus))
        gap *= 1.0 - 0.25 * min(1.0, s["start"] / max(self.drop, 1.0))  # the whole run-up warms slowly
        seq = {"intro": ["comet"], "verse": ["comet", "ring", "comet", "willow"]}.get(
            name, ["willow", "comet", "ringpair" if k_chorus >= 1 else "ring", "comet"])
        lanes = [0.0, -30.0, 30.0, -55.0, 55.0, -15.0, 15.0, -72.0, 72.0]
        last, k = -1e9, 0
        placed = []
        times = []
        t = a
        while True:  # every `gap` seconds, on a downbeat when one is close enough, else on a beat
            dbs = self.downbeats_in(max(t, last + gap * 0.95), b)
            bts = self.beats_in(max(t, last + gap * 0.95), b)
            if len(bts) == 0:
                break
            t = float(dbs[0]) if len(dbs) and dbs[0] - bts[0] <= 0.3 * gap else float(bts[0])
            times.append(t)
            last = t
        last = -1e9
        for t in times:
            kind = seq[k % len(seq)]
            x = lanes[self.nth("pre_lane") % len(lanes)]
            p = t / max(self.drop, 1.0)
            if kind == "comet":
                self.add(t, "comet", x + self.j(3), self.row("mid") + self.j(4), ["silver", "cyan"][k % 2], 0.8,
                         80, h=64 + 30 * p)
            elif kind == "willow":
                self.add(t, "willow", x + self.j(3), self.row("back") + self.j(3), ["gold", "silver"][k_chorus % 2],
                         0.9 + 0.1 * min(k_chorus, 2), 80, h=104)
            elif kind == "ring":
                self.add(t, "ring", x + self.j(3), self.row("mid") + self.j(4), ["blue", "cyan", "purple"][k % 3],
                         0.65, 80, h=78 + 10 * p)
            else:
                self.pair(t, "ring", 40 + 10 * (k % 3), self.row("mid"), ["cyan", "purple"][k % 2], 0.65, 80,
                          h=80 + 10 * p)
            placed.append(t)
            last, k = t, k + 1
        # one accent on the strongest hit of the section ("single shot")
        strong = [(hs, ht) for ht, hs in self.hits if a <= ht < b and hs >= 0.8
                  and all(abs(ht - q) > 1.5 for q in placed)]
        if strong and name != "intro":
            _, ht = max(strong)
            self.add(self.snap(ht), "ring", [-45.0, 45.0][self.nth("pre_acc") % 2], self.row("front"), "silver",
                     0.6, 60, h=72)

    def gen_pre_build(self, s: dict, drop: float) -> None:
        """Comets speeding up (bar -> half bar -> beat) and climbing; then 1-2 empty beats."""
        P = self.local_period(drop - 1.0)
        n_gap = 1 if P >= 0.75 else 2
        stop = drop - n_gap * P + 1e-3
        beats = self.beats_in(max(s["start"], C.FIRST_CUE_MIN_T), stop - 2e-3)
        if len(beats) == 0:
            return
        dbs = self.downbeats_in(s["start"], drop)
        ref = float(dbs[0]) if len(dbs) else float(beats[0])
        i_ref = int(np.argmin(np.abs(self.beats - ref)))
        i0 = int(np.argmin(np.abs(self.beats - beats[0])))
        L = max(stop - s["start"], 1e-6)
        zig = [-50.0, -20.0, 10.0, 40.0, 60.0, 25.0, -5.0, -35.0]
        k = 0
        for j, t in enumerate(beats):
            p = (t - s["start"]) / L
            step = 4 if p < 0.45 else 2 if p < 0.8 else 1
            if (i0 + j - i_ref) % step:
                continue
            self.add(t, "comet", zig[k % len(zig)] + self.j(3), self.row("mid") + self.j(4),
                     "silver" if p < 0.6 else "gold", 0.9 + 0.3 * p, 80, h=60 + 56 * p)
            if p > 0.6 and self.is_downbeat(t):
                self.pair(t, "comet", 65, self.row("back"), "gold", 1.0, 75, h=80 + 30 * p)
            k += 1

    def eruption(self, drop: float) -> float:
        """The drop is not one bang but ~2 bars of eruption (8-10 shells/s, a shell on every beat): a
        full-width salvo on the drop, then mirrored big chrysanthemum/peony/crossette volleys on every
        beat spreading out to both edges, and a gold willow curtain on the last beat. Returns the end."""
        P = self.local_period(drop + 2.0)
        beats = self.beats_in(drop, drop + 8 * P - 0.5 * P)
        if len(beats) == 0 or abs(beats[0] - drop) > 1e-3:
            beats = np.concatenate([[drop], beats[beats > drop + 1e-3]])
        per_beat = int(np.clip(round(9.0 * P), 3, 6))  # ~9 shells/s whatever the tempo
        pals = ["mixed", "gold", "red", "silver", "mixed", "blue", "gold", "pink", "mixed"]
        kinds = ["peony", "chrysanthemum"]
        # beat 0: eight big shells across the whole sky (mirrored, +-12 .. +-84 m). Eight, not more:
        # the next two beats must still get a mirrored pair each inside the same 12-per-second window.
        for i, x in enumerate((-84.0, -60.0, -36.0, -12.0, 12.0, 36.0, 60.0, 84.0)):
            inner = abs(x) < 20
            self.add(drop, "chrysanthemum" if inner or i % 2 else "peony", x, 20.0 if i % 2 == 0 else 4.0,
                     "gold" if inner else pals[i], 1.5 if inner else 1.4, 100,
                     h=112 if inner else (104 if i % 2 else 92), hit=(i == 3))
        last = float(beats[-1])
        bts = [float(b) for b in beats]
        counts = self._eruption_counts(bts, opening=8, curtain=7 if len(bts) >= 3 else 0)
        if len(bts) >= 3:
            for i, x in enumerate(np.linspace(-84, 84, 7)):
                self.add(last, "willow", float(x), 24.0 if i % 2 == 0 else 8.0, "gold", 1.35, 100,
                         h=116 if i % 2 == 0 else 106, hit=(i == 3))
        kinds3 = ["chrysanthemum", "peony", "crossette"]
        mids = bts[1:-1]
        for j, t in enumerate(mids):
            n = counts[j + 1]
            reach = 30.0 + 56.0 * j / max(1, len(mids) - 1)  # volleys spread out to both edges
            q = 0
            while n >= 2:
                x = min(86.0, reach + 22.0 * q) if q % 2 == 0 else max(14.0, reach - 22.0 * ((q + 1) // 2))
                self.pair(t, kinds3[(j + q) % 3], x, self.row("back" if (j + q) % 2 else "mid"),
                          pals[(j + q) % len(pals)], 1.3, 95, h=112 if (j + q) % 2 else 92)
                n -= 2
                q += 1
            if n == 1:
                self.add(t, kinds3[j % 3], self.j(4), self.row("back"), pals[j % len(pals)], 1.4, 95, h=115)
        return last + P

    # ----- after the drop
    def _pattern(self, pat: str, t: float, n: int, k: int, theme: dict, phrase: int, scale: float,
                 state: dict) -> None:
        """Place n shells on beat t; k = beat index inside the 4-bar phrase."""
        types, pals = theme["types"], theme["pals"]
        typ = types[(k + phrase) % len(types)]
        pal = pals[(k // 2 + phrase) % len(pals)]
        if pat == "sweep":
            for j in range(n):
                x = state["x"]
                state["x"] += 17.0 * state["dir"]
                if abs(state["x"]) > 80:
                    state["dir"] *= -1
                    state["x"] = float(np.clip(state["x"], -80, 80))
                self.add(t, types[(k + j) % len(types)], x + self.j(3), self.row("mid") + self.j(5), pal, scale, 50)
        elif pat == "callresp":
            side = -1.0 if (k // 2) % 2 == 0 else 1.0
            for j in range(n):
                self.add(t, typ, side * (28 + 20 * j) + self.j(4), self.row("back" if j % 2 else "mid") + self.j(3),
                         pal, scale, 50)
        elif pat == "fan":  # exactly n shells: centre on the phrase beat, pairs moving outwards
            r = 22.0 * (k % 4)
            if r == 0 and n:
                self.add(t, typ, self.j(2), self.row("mid"), pal, scale, 50)
                n -= 1
            j = 0
            while n >= 2:
                self.pair(t, typ, max(r, 22.0) + 14 * j, self.row("front" if j % 2 else "mid"), pal, scale, 50)
                n -= 2
                j += 1
            if n == 1:
                self.add(t, typ, (max(r, 22.0) + 14 * j) * (1 if k % 2 else -1), self.row("mid"), pal, scale, 50)
        elif pat == "pulse":  # exactly n shells: mirrored pairs on even beats, glitter on odd beats
            if k % 2 == 0:
                j = 0
                while n >= 2:
                    self.pair(t, types[0], 36 + 16 * j, self.row("mid"), pal, scale, 50)
                    n -= 2
                    j += 1
                if n == 1:
                    self.add(t, types[0], self.j(6), self.row("back"), pal, scale, 50)
            else:  # off-beats: glitter, alternating depth rows so it never repeats from one spot
                row = self.row("front" if (k // 2) % 2 else "back")
                self.add(t, "strobe", self.j(10), row, "silver", scale * 0.8, 45, h=68)
                n -= 1
                j = 0
                while n >= 2:
                    self.pair(t, "strobe", 18 + 14 * j + 8 * ((k // 2) % 2), row, "silver", scale * 0.8, 45, h=70)
                    n -= 2
                    j += 1
                if n == 1:
                    self.add(t, "strobe", 40.0 * (1 if k % 4 == 1 else -1), row, "silver", scale * 0.8, 45, h=72)
        else:  # chain: a lead shell stepping across the sky, high / low, with company when the budget allows
            d = 1.0 if phrase % 2 == 0 else -1.0
            x = d * (-78 + 10.4 * (k % 16))
            self.add(t, typ, x, self.row("back" if k % 2 else "front"), pal, scale, 50,
                     h=self.height(typ, 0.9 if k % 2 else 0.2))
            for j in range(1, n):
                xj = float(np.clip(x + 26.0 * ((j + 1) // 2) * (-1) ** j, -84, 84))
                self.add(t, types[(k + j) % len(types)], xj, self.row("mid") + self.j(3), pal, scale * 0.9, 45)

    def _room(self, t: float, cap: int) -> int:
        """How many aerial shells may still go on beat t: cap minus those already placed in (t-1 s, t].
        Checking the trailing second at every beat keeps every 1 s window within the cap."""
        n = sum(1 for c in self.cues if c.type != "fountain" and t - C.RATE_WINDOW + 1e-6 < c.t <= t + 1e-9)
        return max(0, cap - n)

    @staticmethod
    def _eruption_counts(bts: list[float], opening: int, curtain: int, cap: int = C.FINALE_MAX_RATE,
                         most: int = 6) -> list[int]:
        """Shells per eruption beat: the opening salvo and the curtain are fixed; every beat in
        between gets at least one shell (no holes), as many as any 1 s window allows, spread evenly
        and preferring even counts (mirrored pairs). Exhaustive search over <= 8 beats."""
        import itertools
        m = len(bts)
        free = list(range(1, m - 1))
        best, best_key = None, None
        for combo in itertools.product(range(1, most + 1), repeat=len(free)):
            c = [opening] + list(combo) + ([curtain] if m > 1 else [])
            ok = True
            for a in range(m):
                if sum(c[b] for b in range(a, m) if bts[b] < bts[a] + C.RATE_WINDOW - 1e-9) > cap:
                    ok = False
                    break
            if not ok:
                continue
            key = (min(combo) if combo else 0, sum(1 for v in combo if v % 2 == 0), sum(combo),
                   -float(np.std(combo)) if combo else 0.0)
            if best_key is None or key > best_key:
                best, best_key = c, key
        return best if best is not None else [opening] + [0] * len(free) + ([curtain] if m > 1 else [])

    def _room_ahead(self, t: float, cap: int) -> int:
        """Like _room, but also keeps the windows that end on shells already placed in (t, t+1 s)."""
        later = sorted({c.t for c in self.cues if c.type != "fountain" and t < c.t < t + C.RATE_WINDOW})
        return min([self._room(t, cap)] + [self._room(u, cap) for u in later])

    def _breath_bar(self, a: float, b: float) -> float | None:
        """The quietest downbeat in the middle of a stage: one bar with (almost) nothing in the sky."""
        dbs = self.downbeats_in(a + 0.3 * (b - a), a + 0.8 * (b - a))
        if len(dbs) < 2:
            return None
        hop = float(self.a["curve_hop"])
        on = np.array(self.a["onset_curve"])
        bar = self.bar
        score = [float(on[int(t / hop) : max(int(t / hop) + 1, int((t + bar) / hop))].mean()) for t in dbs]
        return float(dbs[int(np.argmin(score))])

    def gen_stage(self, s: dict, climax: float, prev_rate: float) -> float:
        i, n = s["stage"], s["stages"]
        final = s["name"] == "finale" or i == n - 1
        a, b = s["start"], min(s["end"], climax)
        self.use_tempo_of(a, b)
        P, bar = self.period, self.bar
        theme = FINALE_THEME if final else THEMES[i % len(THEMES)]
        level = (i + 1) / max(1, n - 1) if not final else 1.0
        n_ch = max(1, n - 1)  # stages before the finale
        # with shells on beats, "6 per any second" allows at most 6 / (beats in a second) per beat:
        # e.g. 4.67 /s at 140 BPM, 5.0 /s at 150 BPM. The chorus stages climb in equal steps up to that
        # ceiling from a floor of STAGE_FLOOR; the finale (cap 12) climbs on to 11 /s.
        r_max = C.MAX_RATE / (int(C.RATE_WINDOW / P) + 1) / P
        step = min(STAGE_STEP, max(0.0, r_max - STAGE_FLOOR) / max(1, n_ch - 1))
        base = (min(r_max, STAGE_FLOOR + step * i) if not final else max(prev_rate, r_max) + 0.5)
        start = max(a, self.erupt_end) if i == 0 else a  # the eruption owns the first two bars
        stop = (b - P + 1e-3) if not final else (climax - P + 1e-3)  # one empty beat before the next step
        dbs = self.downbeats_in(start, stop)
        if len(dbs) == 0:
            return base
        entrance = float(dbs[0]) if i > 0 else None
        # breathing room is the empty beat at the end of every stage (lead 2026-11-18: <= 1 bar between
        # stages); no extra quiet bar inside a stage, so every stage keeps its density
        breath = None
        # fountains: second stage (8 bars) and the whole finale
        if i == 1 and not final:
            self.add(float(dbs[0]), "fountain", -45.0, -6.0, "gold", 1.0, 70, dur=min(8 * bar, b - dbs[0]))
            self.add(float(dbs[0]), "fountain", 45.0, -6.0, "gold", 1.0, 70, dur=min(8 * bar, b - dbs[0]))
        if final:
            for j, x in enumerate((-66.0, -22.0, 22.0, 66.0)):
                self.add(float(dbs[0]), "fountain", x, -6.0, ["gold", "silver"][j % 2], 1.1, 70,
                         dur=max(1.0, min(climax + 2 * P, self.duration - 0.5) - float(dbs[0])))
        beats = self.beats_in(float(dbs[0]), stop)
        L = max(stop - float(dbs[0]), 1e-6)
        acc = 0.0
        cap = C.FINALE_MAX_RATE if final else C.MAX_RATE
        prev_pat, pat = None, None
        state = {"x": -80.0, "dir": 1.0}
        phrase = -1
        k = 0
        rate = base
        n_db = -1
        for t in beats:
            if self.is_downbeat(t):
                n_db += 1
            if (self.is_downbeat(t) and n_db % 4 == 0) or pat is None:
                phrase += 1
                k = 0
                choices = [p for p in PATTERNS if p != prev_pat]
                pat = choices[int(self.rng.integers(len(choices)))]
                prev_pat = pat
                state["dir"] = 1.0 if phrase % 2 == 0 else -1.0
                state["x"] = -80.0 * state["dir"]
                # phrase-opening salvo; a hit on the stage entrance and every other phrase
                big = entrance is not None and abs(t - entrance) < 1e-6
                st_types = theme["salvo"]
                pal = theme["pals"][phrase % len(theme["pals"])]
                n_pairs = 2 if (big or final) else 1
                self.volley(t, n_pairs, st_types, (pal, theme["pals"][(phrase + 1) %
                            len(theme["pals"])]), 90 if big else 85, phrase, scale=1.2 + 0.25 * level)
                acc += rate * P - (1 + 2 * n_pairs)  # the salvo spends this beat's budget and some more
                hit_here = big or phrase % 2 == 0
                if not hit_here:
                    for c in self.cues[::-1]:
                        if c.t == t and c.hit:
                            c.hit = False
                            break
                continue
            k += 1
            if breath is not None and breath <= t < breath + bar / 2:  # half a bar of quiet
                if abs(t - breath) < 1e-6:
                    self.add(t, "willow", self.j(20), self.row("back"), "silver", 1.0, 60, h=110)
                continue
            p = (t - float(dbs[0])) / L
            rate = base if not final else base + (11.0 - base) * p ** 1.3
            acc += rate * P
            m = min(int(acc), self._room(t, cap))  # never more than the trailing second still allows
            acc = min(acc, cap * P + 1.0)
            if m <= 0:
                continue
            acc -= m
            self._pattern(pat, t, m, k, theme, phrase, 0.95 + 0.25 * level + (0.2 * p if final else 0.0), state)
        return rate

    # ----- assemble
    def build(self) -> None:
        drop = self.find_drop()
        self.drop = drop
        climax = self.find_final_hit(drop)
        self.climax = climax
        self.sections = self.plan_sections(drop, climax)
        self.erupt_end = drop  # set by eruption(); stages start after it
        k_chorus = 0
        prev_rate = 0.0
        self.use_tempo_of(drop, drop + 8.0)
        self.erupt_end = self.eruption(drop)
        self.peaks = [(drop, self.erupt_end)]  # the eruption may use the finale cap (12 per second)
        for s in self.sections:
            if s["end"] <= drop + 1e-6:
                self.use_tempo_of(s["start"], s["end"])
                if s["name"] == "build":
                    self.gen_pre_build(s, drop)
                else:
                    self.gen_pre(s, k_chorus)
                    k_chorus += s["name"] == "chorus"
            elif "stage" in s:
                prev_rate = self.gen_stage(s, climax, prev_rate)
        self.use_tempo_of(climax - 4.0, self.duration)
        self.curtain(climax)
        self.gen_after_climax(climax)
        self.limit_rate()
        self.fix_repeats()
        for s in self.sections:
            for k in ("music", "stage", "stages"):
                s.pop(k, None)


def dump(show: dict) -> str:
    """Readable JSON: one cue per line."""
    head = {k: v for k, v in show.items() if k not in ("sections", "cues")}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f'  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},')
    lines.append('  "sections": [')
    lines.append(",\n".join("    " + json.dumps(s) for s in show["sections"]))
    lines.append("  ],")
    lines.append('  "cues": [')
    lines.append(",\n".join("    " + json.dumps(c) for c in show["cues"]))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def choreograph(analysis: dict, music: str, credit: str, seed: int, labels: list[str] | None = None,
                boundaries: list[float] | None = None, style: str = "default", drop: float | None = None,
                climax: float | None = None) -> dict:
    if style == "slowburn":
        show = SlowBurn(analysis, seed, labels, boundaries, drop, climax)
    else:
        show = Show(analysis, seed, labels, boundaries)
    show.build()
    return show.to_json(music, credit, seed)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--music", required=True, help="file name Unity loads from Assets/Fireworks/Audio/Music/")
    ap.add_argument("--credit", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--labels", default=None, help="comma-separated section names to override the automatic ones")
    ap.add_argument("--boundaries", default=None,
                    help="comma-separated section start times in seconds (snapped to bars) to replace the detected ones")
    ap.add_argument("--style", choices=["default", "slowburn"], default="default",
                    help="slowburn: sparse until the drop, then step-by-step growth to the final hit")
    ap.add_argument("--drop", type=float, default=None, help="slowburn: drop time in seconds (default: estimated)")
    ap.add_argument("--climax", type=float, default=None,
                    help="slowburn: final big hit in seconds (default: estimated)")
    args = ap.parse_args()
    a = json.loads(Path(args.analysis).read_text())
    labels = [x.strip() for x in args.labels.split(",")] if args.labels else None
    bnds = [float(x) for x in args.boundaries.split(",")] if args.boundaries else None
    show = choreograph(a, args.music, args.credit, args.seed, labels, bnds, args.style, args.drop, args.climax)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(show), encoding="utf-8")
    counts: dict[str, int] = {}
    for c in show["cues"]:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    print(f"{len(show['cues'])} cues -> {out}" + (f"  (drop {show['drop']:.2f} s)" if "drop" in show else ""))
    print("  sections: " + ", ".join(f"{s['name']}({s['start']:.1f}-{s['end']:.1f}, e={s['energy']:.2f})"
                                     for s in show["sections"]))
    print("  types: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
