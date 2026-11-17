#!/usr/bin/env python3
"""Render the pipeline test song (synthesised, copyright-free) and its ground truth.

    python3 Tools/make_test_song.py
      -> Assets/Fireworks/Audio/Music/test_song.wav   (44.1 kHz, 16-bit, mono, ~91.5 s)
      -> Tools/out/test_song_truth.json               (beats, downbeats, section boundaries)

120 BPM, 4/4, A minor. The first downbeat is at 0.30 s (a short lead-in, as in real files).
Sections (bars): intro 0-8 soft pad + pluck, build 8-16 snare roll + riser, chorus 16-28
full kit + bass + lead, bridge 28-34 quiet, finale 34-42 loud, final hit at bar 42, outro fade.

    python3 Tools/make_test_song.py --song slowburn
      -> Assets/Fireworks/Audio/Music/test_song_slowburn.wav (~5 min: 60 BPM ballad, then 140 BPM)
      -> Tools/out/test_song_slowburn_truth.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import dsp
from dsp import SR, exp_env, norm_peak

ROOT = Path(__file__).resolve().parents[1]
OUT_WAV = ROOT / "Assets" / "Fireworks" / "Audio" / "Music" / "test_song.wav"
OUT_TRUTH = ROOT / "Tools" / "out" / "test_song_truth.json"

BPM = 120.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT
OFFSET = 0.30
N_BARS = 45
DURATION = OFFSET + N_BARS * BAR + 1.2
SECTIONS = [("intro", 0, 8), ("build", 8, 16), ("chorus", 16, 28), ("bridge", 28, 34), ("finale", 34, 42),
            ("outro", 42, 45)]

CHORDS = {  # pad voicing (MIDI), bass root (MIDI)
    "Am": ([57, 60, 64], 33),
    "F": ([57, 60, 65], 29),
    "C": ([55, 60, 64], 36),
    "G": ([55, 59, 62], 31),
    "Em": ([55, 59, 64], 28),
}
PROG = ["Am", "F", "C", "G"]
BRIDGE_PROG = ["F", "G", "Em", "Am", "F", "G"]

# 4-bar lead motif: (beat offset, length in beats, MIDI)
MOTIF = [(0, 1, 76), (1, .5, 74), (1.5, .5, 72), (2, 1.5, 69), (3.5, .5, 72),
         (4, 1, 72), (5, .5, 69), (5.5, .5, 72), (6, 2, 77),
         (8, 1, 76), (9, .5, 79), (9.5, .5, 76), (10, 1, 74), (11, 1, 72),
         (12, 1.5, 74), (13.5, .5, 71), (14, 2, 74)]

rng = np.random.default_rng(2026)
N = int(DURATION * SR)


def bar_t(bar: float) -> float:
    return OFFSET + bar * BAR


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def section_of(bar: int) -> str:
    for name, a, b in SECTIONS:
        if a <= bar < b:
            return name
    return "outro"


def chord_at(bar: int) -> str:
    if 28 <= bar < 34:
        return BRIDGE_PROG[bar - 28]
    if bar >= 42:
        return "Am"
    return PROG[bar % 4]


# --------------------------------------------------------------------------- instruments

_TABLES: dict[tuple[str, int], np.ndarray] = {}


def _table(kind: str, n_harm: int) -> np.ndarray:
    key = (kind, n_harm)
    if key not in _TABLES:
        ph = np.arange(4096) / 4096
        w = np.zeros(4096)
        for h in range(1, n_harm + 1):
            if kind == "square" and h % 2 == 0:
                continue
            w += np.sin(2 * np.pi * h * ph) / h
        _TABLES[key] = np.append(w / np.max(np.abs(w)), w[0] / np.max(np.abs(w)))
    return _TABLES[key]


def osc(kind: str, f: float | np.ndarray, n: int, phase0: float = 0.0) -> np.ndarray:
    """Band-limited wavetable oscillator (no aliasing up to ~11 kHz)."""
    fmax = float(np.max(f))
    tab = _table(kind, max(1, min(40, int(11000 / fmax))))
    inc = np.broadcast_to(np.asarray(f, float), (n,)) / SR
    ph = (phase0 + np.cumsum(inc)) % 1.0
    return np.interp(ph * 4096, np.arange(4097), tab)


def adsr(n: int, a: float, d: float, s: float, r: float, gate: float) -> np.ndarray:
    t = np.arange(n) / SR
    env = np.where(t < a, t / a, s + (1 - s) * np.exp(-(t - a) / max(d, 1e-3)))
    rel = np.clip(1 - (t - gate) / r, 0, 1)
    return env * np.where(t > gate, rel, 1.0)


def pad_note(m: int, length: float, attack=0.35, release=0.6) -> np.ndarray:
    n = int((length + release) * SR)
    x = np.zeros(n)
    for cents in (-8, 0, 8):
        x += osc("saw", hz(m) * 2 ** (cents / 1200), n, rng.uniform())
    x += 0.25 * np.sin(2 * np.pi * hz(m - 12) * np.arange(n) / SR)
    return x / 3.5 * adsr(n, attack, 0.5, 0.85, release, length)


def pluck(m: int, length=0.7) -> np.ndarray:
    n = int(length * SR)
    t = np.arange(n) / SR
    x = sum((1 / h ** 1.3) * np.sin(2 * np.pi * h * hz(m) * t) * np.exp(-t * (3 + 2.5 * h)) for h in range(1, 7))
    return x * np.clip(t / 0.003, 0, 1) * np.clip((length - t) / 0.05, 0, 1)


def bass_note(m: int, length: float) -> np.ndarray:
    n = int((length + 0.03) * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * hz(m) * t) + 0.35 * dsp.lowpass(osc("saw", hz(m + 12), n), 500)
    return x * adsr(n, 0.005, 0.15, 0.7, 0.03, length)


def lead_note(m: int, length: float) -> np.ndarray:
    n = int((length + 0.12) * SR)
    t = np.arange(n) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - 0.15) / 0.2, 0, 1)
    x = 0.6 * osc("square", hz(m) * vib, n) + 0.4 * osc("saw", hz(m) * vib * 1.003, n)
    return dsp.lowpass(x, 3200) * adsr(n, 0.012, 0.2, 0.75, 0.1, length)


def kick(gain=1.0) -> np.ndarray:
    t = np.arange(int(0.45 * SR)) / SR
    f = 45 + 105 * np.exp(-t / 0.035)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.16)
    click = dsp.highpass(rng.standard_normal(len(t)) * np.exp(-t / 0.002), 2000) * 0.25
    return gain * (body + click)


def snare(gain=1.0, tone=190.0, length=0.25) -> np.ndarray:
    t = np.arange(int(length * SR)) / SR
    noise = dsp.bandpass(rng.standard_normal(len(t)), 1200, 8000) * np.exp(-t / 0.07)
    body = np.sin(2 * np.pi * tone * t) * np.exp(-t / 0.05)
    return gain * (0.9 * norm_peak(noise) + 0.6 * body)


def hat(gain=1.0, open_=False) -> np.ndarray:
    length = 0.35 if open_ else 0.06
    t = np.arange(int(length * SR)) / SR
    x = dsp.highpass(rng.standard_normal(len(t)), 7500, order=4) * np.exp(-t / (0.12 if open_ else 0.018))
    return gain * norm_peak(x)


def crash(gain=1.0) -> np.ndarray:
    t = np.arange(int(2.5 * SR)) / SR
    x = dsp.highpass(rng.standard_normal(len(t)), 3500, order=2) * np.exp(-t / 0.7)
    metal = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (3120, 4370, 5230, 6640)) * np.exp(-t / 0.5)
    return gain * norm_peak(x + 0.08 * metal)


def tom(f0: float, gain=1.0) -> np.ndarray:
    t = np.arange(int(0.4 * SR)) / SR
    f = f0 * (1 + 0.5 * np.exp(-t / 0.03))
    return gain * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.14)


# --------------------------------------------------------------------------- arrangement

def render() -> np.ndarray:
    t = np.arange(N) / SR
    pad = np.zeros(N)
    plk = np.zeros(N)
    bass = np.zeros(N)
    lead = np.zeros(N)
    drums = np.zeros(N)
    fx = np.zeros(N)
    kick_env = np.zeros(N)

    for bar in range(N_BARS):
        sec = section_of(bar)
        tones, root = CHORDS[chord_at(bar)]
        t0 = bar_t(bar)
        # pad (held one bar, overlapping release)
        length = BAR * (3 if bar == 42 else 1)
        if bar < 43:
            for m in tones:
                dsp.place(pad, pad_note(m, length, attack=0.02 if bar == 42 else 0.35,
                                        release=2.5 if bar == 42 else 0.6), t0)
        # pluck arpeggio
        if sec in ("intro", "build"):
            for k in range(4):
                dsp.place(plk, 0.5 * pluck(tones[[0, 1, 2, 1][k]] + 12), t0 + k * BEAT)
        elif sec == "bridge":
            for k in range(8):
                dsp.place(plk, 0.4 * pluck(tones[[0, 1, 2, 1, 2, 0, 1, 2][k]] + 12 + (12 if k == 4 else 0)),
                          t0 + k * BEAT / 2)
        elif sec == "outro" and bar < 44:
            dsp.place(plk, 0.35 * pluck(tones[2] + 12, 1.5), t0 + BAR / 2 + 0.5)
        # bass
        if sec in ("chorus", "finale"):
            for k in range(8):
                dsp.place(bass, bass_note(root + (12 if k in (3, 7) else 0), BEAT / 2 * 0.8), t0 + k * BEAT / 2)
        elif sec == "build" and bar >= 12:
            for k in range(4):
                dsp.place(bass, 0.6 * bass_note(root, BEAT * 0.6), t0 + k * BEAT)
        # drums
        if sec in ("chorus", "finale"):
            loud = 1.0 if sec == "chorus" else 1.15
            for k in range(4):
                k_t = t0 + k * BEAT
                dsp.place(drums, kick(loud), k_t)
                dsp.place(kick_env, np.exp(-np.arange(int(0.3 * SR)) / SR / 0.09), k_t)
                if k in (1, 3):
                    dsp.place(drums, snare(0.8 * loud), k_t)
            for k in range(16 if sec == "finale" else 8):
                step = BEAT / (4 if sec == "finale" else 2)
                dsp.place(drums, hat(0.18 if k % 2 else 0.25), t0 + k * step)
            if sec == "finale":
                for k in range(4):
                    dsp.place(drums, hat(0.2, open_=True), t0 + k * BEAT + BEAT / 2)
            if bar in (16, 24, 34, 38):
                dsp.place(drums, crash(0.55), t0)
            if bar == 41:  # tom fill into the final hit
                for k, f0 in enumerate([180, 180, 150, 150, 120, 120, 95, 95]):
                    dsp.place(drums, tom(f0, 0.7 + 0.04 * k), t0 + 2 * BEAT + k * BEAT / 4)
        if sec == "build":
            i = bar - 8
            per_bar = [4, 4, 4, 4, 8, 8, 16, 16][i]
            for k in range(per_bar):
                when = t0 + k * BAR / per_bar
                prog = (when - bar_t(8)) / (8 * BAR)
                dsp.place(drums, snare(0.18 + 0.55 * prog ** 1.5, tone=180 + 80 * prog, length=0.18), when)
        # leads
        if sec in ("chorus", "finale"):
            phrase_bar = (bar - (16 if sec == "chorus" else 34)) % 4
            for beat, dur, m in MOTIF:
                if int(beat // 4) == phrase_bar:
                    when = t0 + (beat % 4) * BEAT
                    dsp.place(lead, lead_note(m, dur * BEAT * 0.9), when)
                    if sec == "finale":
                        dsp.place(lead, 0.35 * lead_note(m + 12, dur * BEAT * 0.9), when)

    # final hit: kick + crash + sub drop on bar 42
    th = bar_t(42)
    dsp.place(drums, 1.4 * kick(), th)
    dsp.place(drums, crash(0.9), th)
    tt = np.arange(int(2.5 * SR)) / SR
    dsp.place(fx, 0.9 * np.sin(2 * np.pi * np.cumsum(32 + 40 * np.exp(-tt / 0.25)) / SR) * np.exp(-tt / 0.8), th)

    # riser over the last half of the build
    a, b = bar_t(12), bar_t(16)
    seg = (t >= a) & (t < b)
    pr = np.zeros(N)
    pr[seg] = (t[seg] - a) / (b - a)
    nz = rng.standard_normal(N) * seg
    riser = dsp.svf(nz, 400 + 7000 * pr ** 2, q=1.5, mode="bp") * pr ** 2
    fx += 0.25 * norm_peak(riser)

    # pad filter + level automation per section
    cut = np.interp(t, [0, bar_t(8), bar_t(16), bar_t(16) + 0.01, bar_t(28), bar_t(28) + 0.01, bar_t(34),
                        bar_t(34) + 0.01, bar_t(42), DURATION],
                    [900, 900, 4200, 2600, 2600, 1100, 1100, 3600, 3600, 700])
    pad = dsp.svf(pad, cut, q=0.9)
    pad_lvl = np.interp(t, [0, bar_t(8), bar_t(16), bar_t(16) + 0.01, bar_t(28), bar_t(28) + 0.01, bar_t(34),
                            bar_t(34) + 0.01, bar_t(42), DURATION],
                        [0.3, 0.3, 0.6, 0.55, 0.55, 0.28, 0.28, 0.65, 0.7, 0.7])
    duck = 1 - 0.35 * np.clip(kick_env, 0, 1)
    pad = pad * pad_lvl * duck
    bass = bass * 0.55 * duck
    plk = dsp.lowpass(plk, 4500) * 0.55
    lead = lead * 0.32
    lead = lead + 0.3 * np.concatenate([np.zeros(int(0.375 * SR)), lead[: N - int(0.375 * SR)]])
    drums = drums * 0.55

    send = 0.3 * pad + 0.45 * plk + 0.35 * lead + 0.12 * drums + 0.3 * fx
    ir = dsp.make_ir(rng, t60_low=2.4, t60_high=1.4, predelay=0.025)
    wet = dsp.convolve(send, ir, N)
    wet *= 0.35 * np.std(send) / max(np.std(wet), 1e-9)
    mix = pad + plk + bass + lead + drums + 0.5 * fx + wet

    # outro fade after the final hit's attack, and a click-free start
    fade = np.clip(1 - (t - (th + 1.0)) / (DURATION - th - 1.0), 0, 1) ** 1.6
    mix *= np.where(t > th + 1.0, fade, 1.0)
    mix = dsp.highpass(mix, 30.0)
    mix = norm_peak(mix)
    return np.tanh(1.3 * mix) / np.tanh(1.3)


def truth() -> dict:
    beats = [round(OFFSET + k * BEAT, 4) for k in range(N_BARS * 4 + 1)]
    downbeats = [round(bar_t(b), 4) for b in range(N_BARS + 1)]
    secs = []
    for name, a, b in SECTIONS:
        secs.append({"name": name, "start": 0.0 if a == 0 else round(bar_t(a), 4),
                     "end": round(DURATION if b == N_BARS else bar_t(b), 4)})
    return {"bpm": BPM, "offset": OFFSET, "beat_period": BEAT, "duration": round(DURATION, 4), "beats": beats,
            "downbeats": downbeats, "sections": secs, "boundaries": [s["start"] for s in secs[1:]],
            "final_hit": round(bar_t(42), 4)}


# =========================================================================== slow-burn test song
# 60 BPM ballad (~3 min) -> 140 BPM high-energy part (~2 min) -> big final hit -> short tail.
# Exercises tempo-change tracking and the "slowburn" show style. Seeded separately, so the
# default song above is unaffected.

SB_WAV = ROOT / "Assets" / "Fireworks" / "Audio" / "Music" / "test_song_slowburn.wav"
SB_TRUTH = ROOT / "Tools" / "out" / "test_song_slowburn_truth.json"
SB_OFFSET = 0.40
SB_SLOW_BPM, SB_FAST_BPM = 60.0, 140.0
SB_SLOW = [("intro", 0, 6), ("verse", 6, 14), ("chorus", 14, 22), ("verse", 22, 30), ("chorus", 30, 38),
           ("build", 38, 45)]  # bars at 60 BPM
SB_FAST = [("chorus", 0, 16), ("chorus", 16, 32), ("chorus", 32, 48), ("finale", 48, 68)]  # bars at 140 BPM
SB_TAIL = 5.5
SB_SLOW_PROG = ["G", "D", "Em", "C", "G", "D", "C", "D"]
SB_FAST_PROG = ["G", "Bb", "F", "F"]
SB_CHORDS = {"G": ([55, 59, 62], 31), "D": ([57, 62, 66], 38), "Em": ([55, 59, 64], 28), "C": ([55, 60, 64], 36),
             "Bb": ([58, 62, 65], 34), "F": ([57, 60, 65], 29)}
SB_PENTA = [55, 58, 60, 62, 65, 67, 70, 72, 74, 77, 79, 82, 84]  # G minor pentatonic


def sb_timeline():
    bars = []
    t = SB_OFFSET
    for name, a, b in SB_SLOW:
        for i in range(a, b):
            bars.append(dict(t=t, beat=60 / SB_SLOW_BPM, part="slow", sec=name, sec_i=i - a, sec_n=b - a, idx=i,
                             chord=SB_SLOW_PROG[i % 8], stage=-1))
            t += 4 * 60 / SB_SLOW_BPM
    drop = t
    for k, (name, a, b) in enumerate(SB_FAST):
        for i in range(a, b):
            bars.append(dict(t=t, beat=60 / SB_FAST_BPM, part="fast", sec=name, sec_i=i - a, sec_n=b - a, idx=i,
                             chord=SB_FAST_PROG[i % 4], stage=k))
            t += 4 * 60 / SB_FAST_BPM
    return bars, drop, t


def sb_render() -> np.ndarray:
    global rng
    rng = np.random.default_rng(2027)
    bars, drop, final_hit = sb_timeline()
    dur = final_hit + SB_TAIL
    n = int(dur * SR)
    t = np.arange(n) / SR
    pad, plk, bass, lead, drums, fx = (np.zeros(n) for _ in range(6))
    chorus_seen = 0
    for bar in bars:
        tones, root = SB_CHORDS[bar["chord"]]
        t0, bt, sec, si = bar["t"], bar["beat"], bar["sec"], bar["sec_i"]
        bl = 4 * bt
        if bar["part"] == "slow":
            if sec == "chorus" and si == 0:
                chorus_seen += 1
            lvl = {"intro": 0.22, "verse": 0.28, "chorus": 0.4, "build": 0.34}[sec]
            for m in tones:
                dsp.place(pad, lvl * pad_note(m, bl, attack=0.8, release=1.2), t0)
            for k in range(4):  # quarter-note arpeggio
                dsp.place(plk, (0.45 if sec == "intro" else 0.35) * pluck(tones[[0, 1, 2, 1][k]] + 12, 1.2),
                          t0 + k * bt)
            if sec != "intro":
                dsp.place(bass, 0.45 * bass_note(root, bl * 0.48), t0)
                dsp.place(bass, 0.4 * bass_note(root + 7, bl * 0.45), t0 + 2 * bt)
                g = 0.35 if sec == "verse" else 0.5
                for k in range(4):
                    if k in (0, 2):
                        dsp.place(drums, kick(g), t0 + k * bt)
                    else:
                        dsp.place(drums, snare(g * 0.7), t0 + k * bt)
                    dsp.place(drums, hat(0.07 * (1.5 if sec == "chorus" else 1)), t0 + k * bt + bt / 2)
                if sec == "chorus" and si == 0:
                    dsp.place(drums, crash(0.4), t0)
            if sec == "chorus":  # slow sung-like melody: two long notes per bar
                for k, m in enumerate((tones[2] + 12, tones[1] + 12 + (2 if si % 2 else 0))):
                    dsp.place(lead, 0.5 * lead_note(m, 2 * bt * 0.9), t0 + k * 2 * bt)
            if sec == "build" and si >= bar["sec_n"] - 3:  # snare 8ths + toms into the drop
                j = si - (bar["sec_n"] - 3)
                for k in range(8):
                    dsp.place(drums, snare(0.25 + 0.15 * j + 0.03 * k, length=0.2), t0 + k * bt / 2)
                if j == 2:
                    for k, f0 in enumerate([200, 200, 170, 170, 140, 140, 110, 110, 95, 95, 85, 85, 75, 75, 70, 70]):
                        dsp.place(drums, tom(f0, 0.5 + 0.03 * k), t0 + k * bt / 4)
        else:
            st = bar["stage"]
            loud = [0.9, 1.0, 1.1, 1.2][st]
            for k in range(4):
                kt = t0 + k * bt
                if k in (0, 2):
                    dsp.place(drums, kick(loud), kt)
                else:
                    dsp.place(drums, snare(0.8 * loud), kt)
                if k == 1:
                    dsp.place(drums, kick(0.6 * loud), kt + bt / 2)
                for h in range(4 if st >= 2 else 2):
                    dsp.place(drums, hat(0.15 if h % 2 else 0.22), kt + h * bt / (4 if st >= 2 else 2))
                # off-beat chord stabs
                for m in tones:
                    dsp.place(pad, 0.35 * loud * pad_note(m, bt * 0.35, attack=0.005, release=0.08), kt + bt / 2)
            if si == 0 or (st == 3 and si % 8 == 0):
                dsp.place(drums, crash(0.5 * loud), t0)
            if si == bar["sec_n"] - 1:
                for k, f0 in enumerate([180, 160, 140, 120]):
                    dsp.place(drums, tom(f0, 0.7), t0 + 3 * bt + k * bt / 4)
            for k in range(8):
                dsp.place(bass, 0.8 * bass_note(root + (12 if k % 4 == 3 else 0), bt / 2 * 0.8), t0 + k * bt / 2)
            # lead "guitar" runs: 8ths -> 16ths, harmony / octave in later stages
            per_beat = [2, 2, 4, 4][st] if not (st == 1 and si % 2) else 4
            pos = 5 + int(rng.integers(0, 3))
            for k in range(4 * per_beat):
                step = int(rng.choice([-2, -1, 1, 1, 2])) if k < 2 * per_beat else int(rng.choice([-2, -1, -1, 1, 2]))
                pos = int(np.clip(pos + step, 0, len(SB_PENTA) - 1))
                m = SB_PENTA[pos]
                when = t0 + k * bt / per_beat
                ln = np.tanh(2.5 * lead_note(m, bt / per_beat * 0.85)) * 0.5
                dsp.place(lead, ln, when)
                if st >= 2:
                    dsp.place(lead, 0.35 * np.tanh(2.5 * lead_note(m + (12 if st == 3 else -5), bt / per_beat * 0.85)),
                              when)
    # riser into the drop
    a, b = drop - 12.0, drop
    seg = (t >= a) & (t < b)
    pr = np.zeros(n)
    pr[seg] = (t[seg] - a) / (b - a)
    i0, i1 = int(a * SR), int(b * SR)
    fx[i0:i1] += 0.3 * norm_peak(dsp.svf(rng.standard_normal(i1 - i0), 400 + 7000 * pr[i0:i1] ** 2, q=1.5,
                                         mode="bp")) * pr[i0:i1] ** 2
    # final hit
    dsp.place(drums, 1.5 * kick(), final_hit)
    dsp.place(drums, crash(1.0), final_hit)
    tt = np.arange(int(3.0 * SR)) / SR
    dsp.place(fx, 0.9 * np.sin(2 * np.pi * np.cumsum(32 + 40 * np.exp(-tt / 0.25)) / SR) * np.exp(-tt / 0.9),
              final_hit)
    for m in SB_CHORDS["G"][0]:
        dsp.place(pad, 0.8 * pad_note(m, 2.0, attack=0.01, release=3.0), final_hit)

    pad = dsp.lowpass(pad, 2600)
    lead = dsp.lowpass(lead, 4200) * 0.3
    plk = dsp.lowpass(plk, 4500) * 0.55
    bass = bass * 0.5
    drums = drums * 0.5
    send = 0.3 * pad + 0.45 * plk + 0.3 * lead + 0.1 * drums + 0.3 * fx
    ir = dsp.make_ir(rng, t60_low=2.6, t60_high=1.5, predelay=0.025)
    wet = dsp.convolve(send, ir, n)
    wet *= 0.35 * np.std(send) / max(np.std(wet), 1e-9)
    mix = pad + plk + bass + lead + drums + 0.5 * fx + wet
    fade = np.clip(1 - (t - (final_hit + 1.0)) / (dur - final_hit - 1.0), 0, 1) ** 1.6
    mix *= np.where(t > final_hit + 1.0, fade, 1.0)
    mix = norm_peak(dsp.highpass(mix, 30.0))
    return np.tanh(1.3 * mix) / np.tanh(1.3)


def sb_truth() -> dict:
    bars, drop, final_hit = sb_timeline()
    dur = final_hit + SB_TAIL
    beats = [round(b["t"] + k * b["beat"], 4) for b in bars for k in range(4)] + [round(final_hit, 4)]
    secs, cur = [], None
    for b in bars:
        key = (b["part"], b["sec"], b["idx"] - b["sec_i"])
        if key != cur:
            secs.append({"name": b["sec"], "start": round(b["t"], 4)})
            cur = key
    secs.append({"name": "outro", "start": round(final_hit, 4)})
    secs[0]["start"] = 0.0
    for i, s_ in enumerate(secs):
        s_["end"] = secs[i + 1]["start"] if i + 1 < len(secs) else round(dur, 4)
    return {"bpm": SB_SLOW_BPM, "duration": round(dur, 4), "beat_period": 60 / SB_FAST_BPM,
            "tempo_segments": [{"start": 0.0, "end": round(drop, 4), "bpm": SB_SLOW_BPM},
                               {"start": round(drop, 4), "end": round(dur, 4), "bpm": SB_FAST_BPM}],
            "tempo_split": round(drop, 4), "drop": round(drop, 4), "beats": beats,
            "downbeats": [round(b["t"], 4) for b in bars] + [round(final_hit, 4)],
            "sections": secs, "boundaries": [s_["start"] for s_ in secs[1:]], "final_hit": round(final_hit, 4)}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--song", choices=["default", "slowburn"], default="default",
                    help="slowburn: 60 BPM ballad -> 140 BPM high-energy part (test_song_slowburn.wav)")
    args = ap.parse_args()
    if args.song == "slowburn":
        x, wav, tpath, tr = sb_render(), SB_WAV, SB_TRUTH, sb_truth()
    else:
        x, wav, tpath, tr = render(), OUT_WAV, OUT_TRUTH, truth()
    stats = dsp.write_wav16(wav, x, -1.0)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(json.dumps(tr, indent=1))
    print(json.dumps(stats))
    print(f"wrote {wav}\nwrote {tpath}")


if __name__ == "__main__":
    main()
