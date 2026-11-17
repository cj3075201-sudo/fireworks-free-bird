#!/usr/bin/env python3
"""Synthesise every firework SFX named in Docs/설계.md (deterministic, no samples).

    python3 Tools/sfx/make_sfx.py            # writes Assets/Fireworks/Audio/SFX/*.wav
    python3 Tools/sfx/make_sfx.py --plot     # also Tools/out/sfx_spectrograms.png

All files: 44.1 kHz, 16-bit, mono, peak <= -1 dBFS. Levels are pre-mixed
(boom loudest, whistle/fountain quietest) so Unity can start at volume 1.0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dsp  # noqa: E402
from dsp import SR, exp_env, norm_peak  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "Assets" / "Fireworks" / "Audio" / "SFX"
TOOLS_OUT = ROOT / "Tools" / "out"

# Pre-mixed peak level per category (dBFS). Contract ceiling is -1 dBFS.
PEAK_DB = {"boom": -1.1, "pop": -4.0, "crackle": -7.0, "launch": -6.0, "whistle": -15.0, "fountain": -13.0}


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def _saturate(x: np.ndarray, drive: float) -> np.ndarray:
    x = norm_peak(x)
    return np.tanh(drive * x) / np.tanh(drive)


def _echoes(dry: np.ndarray, taps) -> np.ndarray:
    """Discrete, progressively darker reflections (buildings / hills)."""
    out = np.zeros_like(dry)
    for delay, gain, fc in taps:
        d = int(delay * SR)
        if d < len(dry):
            out[d:] += dsp.lowpass(dry, fc, order=2)[: len(dry) - d] * gain
    return out


# --------------------------------------------------------------------------- boom

def boom(seed: int, p: dict) -> np.ndarray:
    """Distant shell burst: crack + pitched punch + filtered-noise body,
    then air absorption, slap echoes, low rolling rumble and a long outdoor tail."""
    rng = np.random.default_rng(seed)
    t = _t(p["dur"])
    n = len(t)

    def detonation(t0: float) -> np.ndarray:
        on = (t >= t0).astype(float)
        tt = np.maximum(t - t0, 0.0)
        f = p["f_lo"] + (p["f_hi"] - p["f_lo"]) * np.exp(-tt / p["sweep_tau"])
        punch = np.sin(2 * np.pi * np.cumsum(f * on) / SR) * exp_env(tt, 0.002, p["punch_tau"]) * on
        fc = p["body_fc1"] + (p["body_fc0"] - p["body_fc1"]) * np.exp(-tt / p["body_fc_tau"])
        body = dsp.svf(rng.standard_normal(n) * on, fc, q=0.55) * exp_env(tt, 0.004, p["body_tau"])
        crack = dsp.bandpass(rng.standard_normal(n) * np.exp(-tt / 0.0018) * on, 500, p["crack_fc"])
        return p["punch"] * norm_peak(punch) + p["body"] * norm_peak(body) + p["crack"] * norm_peak(crack)

    dry = detonation(0.0)
    if p.get("double"):
        off, g = p["double"]
        dry = dry + g * detonation(off)
    dry = dsp.lowpass(dry, p["air_fc"], order=2)  # distance: highs are absorbed

    ech = _echoes(dry, p["echoes"])
    rumble = dsp.lowpass(rng.standard_normal(n), p["rumble_fc"], order=4)
    am = np.clip(1.0 + 0.45 * dsp.smooth_noise(rng, n, 5.0), 0.2, None)
    rumble = norm_peak(rumble * am * (1 - np.exp(-t / p["rumble_att"])) * np.exp(-t / p["rumble_tau"]))

    ir = dsp.make_ir(rng, t60_low=p["t60"], t60_high=p["t60"] * 0.3, predelay=0.04, split_hz=700)
    wet = dsp.convolve(dry + 0.5 * ech, ir, n)
    wet = norm_peak(wet, p["wet"] * np.max(np.abs(dry)))

    out = dry + ech + p["rumble"] * np.max(np.abs(dry)) * rumble + wet
    out = dsp.highpass(out, 28.0, order=2)
    fade = np.clip((p["dur"] - t) / 1.4, 0.0, 1.0)
    return _saturate(out, p["drive"]) * np.sin(0.5 * np.pi * fade) ** 2


BOOMS = {
    # big, deep shell with a long roll
    "boom_1": dict(dur=4.5, f_hi=120, f_lo=42, sweep_tau=0.05, punch_tau=0.22, body_fc0=2600, body_fc1=180,
                   body_fc_tau=0.15, body_tau=0.32, crack_fc=6000, punch=0.9, body=1.0, crack=0.35, air_fc=5000,
                   echoes=[(0.29, 0.32, 1200), (0.57, 0.20, 900), (1.05, 0.10, 700)],
                   rumble=0.35, rumble_fc=160, rumble_att=0.12, rumble_tau=1.1, t60=2.8, wet=0.45, drive=1.8),
    # punchy, closer, brighter crack, shorter tail
    "boom_2": dict(dur=3.0, f_hi=160, f_lo=58, sweep_tau=0.035, punch_tau=0.13, body_fc0=5000, body_fc1=300,
                   body_fc_tau=0.08, body_tau=0.18, crack_fc=9000, punch=1.0, body=1.0, crack=0.6, air_fc=9000,
                   echoes=[(0.18, 0.25, 2000), (0.41, 0.12, 1200)],
                   rumble=0.2, rumble_fc=200, rumble_att=0.08, rumble_tau=0.6, t60=1.8, wet=0.32, drive=2.2),
    # double report (break charge + star ignition 85 ms later)
    "boom_3": dict(dur=3.8, f_hi=130, f_lo=50, sweep_tau=0.045, punch_tau=0.17, body_fc0=3500, body_fc1=220,
                   body_fc_tau=0.10, body_tau=0.24, crack_fc=7000, punch=0.9, body=1.0, crack=0.45, air_fc=6500,
                   double=(0.085, 0.65),
                   echoes=[(0.24, 0.28, 1500), (0.49, 0.15, 1000), (0.83, 0.08, 800)],
                   rumble=0.28, rumble_fc=180, rumble_att=0.10, rumble_tau=0.9, t60=2.4, wet=0.40, drive=1.9),
    # far away: dull attack, many echoes rolling across the valley
    "boom_4": dict(dur=5.0, f_hi=100, f_lo=40, sweep_tau=0.06, punch_tau=0.28, body_fc0=1500, body_fc1=150,
                   body_fc_tau=0.20, body_tau=0.40, crack_fc=3500, punch=0.9, body=1.0, crack=0.2, air_fc=2800,
                   echoes=[(0.35, 0.35, 900), (0.62, 0.28, 800), (0.95, 0.20, 650), (1.40, 0.12, 500),
                           (1.90, 0.07, 450)],
                   rumble=0.5, rumble_fc=140, rumble_att=0.25, rumble_tau=1.5, t60=3.5, wet=0.50, drive=1.5),
}


# --------------------------------------------------------------------------- pop

def pop(seed: int, p: dict) -> np.ndarray:
    """Small burst: very short bright crack + tight noise body + a little woody tone."""
    rng = np.random.default_rng(seed)
    t = _t(p["dur"])
    n = len(t)

    def one(t0: float) -> np.ndarray:
        on = (t >= t0).astype(float)
        tt = np.maximum(t - t0, 0.0)
        crack = dsp.bandpass(rng.standard_normal(n) * np.exp(-tt / p["crack_tau"]) * on, p["crack_lo"], p["crack_hi"])
        body = dsp.lowpass(rng.standard_normal(n) * on, p["body_fc"], order=2) * exp_env(tt, 0.001, p["body_tau"])
        f = p["tone"] * (1.0 + 0.4 * np.exp(-tt / 0.01))
        tone = np.sin(2 * np.pi * np.cumsum(f * on) / SR) * exp_env(tt, 0.001, p["tone_tau"]) * on
        return p["crack"] * norm_peak(crack) + norm_peak(body) + p["tone_g"] * norm_peak(tone)

    dry = one(0.0)
    if p.get("double"):
        off, g = p["double"]
        dry = dry + g * one(off)
    ech = _echoes(dry, [(p["echo"][0], p["echo"][1], 2500)])
    ir = dsp.make_ir(rng, t60_low=p["t60"], t60_high=p["t60"] * 0.4, predelay=0.02)
    wet = norm_peak(dsp.convolve(dsp.highpass(dry, 400.0), ir, n), p["wet"] * np.max(np.abs(dry)))
    out = dsp.highpass(dry + ech + wet, 60.0, order=2)
    return _saturate(out, 1.6)


POPS = {
    "pop_1": dict(dur=1.0, crack_tau=0.0012, crack_lo=1500, crack_hi=9000, crack=0.8, body_fc=3500, body_tau=0.035,
                  tone=320, tone_tau=0.04, tone_g=0.22, echo=(0.11, 0.18), t60=0.9, wet=0.28),
    "pop_2": dict(dur=1.2, crack_tau=0.0015, crack_lo=1200, crack_hi=7000, crack=0.5, body_fc=2200, body_tau=0.06,
                  tone=210, tone_tau=0.05, tone_g=0.3, echo=(0.14, 0.20), t60=1.1, wet=0.30),
    "pop_3": dict(dur=0.9, crack_tau=0.0009, crack_lo=2500, crack_hi=12000, crack=1.0, body_fc=5000, body_tau=0.025,
                  tone=450, tone_tau=0.025, tone_g=0.18, double=(0.035, 0.5), echo=(0.09, 0.15), t60=0.8, wet=0.25),
}


# --------------------------------------------------------------------------- crackle

def crackle(seed: int, p: dict) -> np.ndarray:
    """Sparse sizzle: a clustered (Cox) process of micro-snaps plus a few bigger pops."""
    rng = np.random.default_rng(seed)
    dur = p["dur"]
    t = _t(dur)
    n = len(t)
    # density envelope: quick rise, plateau, thinning out
    dens = np.clip(t / 0.06, 0, 1) * np.clip((dur - t) / (dur * p["thin"]), 0, 1) ** 1.5
    cluster = np.clip(1.0 + 0.8 * dsp.smooth_noise(rng, n, 8.0), 0.0, None)
    rate = p["rate"] * dens * cluster
    rmax = float(rate.max())
    k = rng.poisson(rmax * dur)
    cand = np.sort(rng.uniform(0, dur, k))
    keep = rng.uniform(0, rmax, k) < rate[np.minimum((cand * SR).astype(int), n - 1)]
    times = cand[keep]

    small = np.zeros(n)
    big = np.zeros(n)
    for when in times:
        is_big = rng.uniform() < p["big_frac"]
        length = rng.uniform(0.003, 0.008) if is_big else rng.uniform(0.0006, 0.0025)
        m = int(length * SR * 3)
        g = rng.standard_normal(m) * np.exp(-np.arange(m) / (length * SR / 2.5))
        amp = rng.lognormal(0.0, 0.45) * (1.5 if is_big else 1.0)
        dsp.place(big if is_big else small, g * amp, when)
    out = norm_peak(dsp.highpass(small, p["hp"], order=2)) + p["big_g"] * norm_peak(dsp.bandpass(big, 500, 7000, order=2))
    out = dsp.lowpass(out, p["air_fc"], order=2)
    ir = dsp.make_ir(rng, t60_low=1.0, t60_high=0.5, predelay=0.02)
    wet = norm_peak(dsp.convolve(out, ir, n + int(0.6 * SR)), p["wet"] * np.max(np.abs(out)))
    out = np.concatenate([out, np.zeros(len(wet) - n)]) + wet
    return _saturate(out, 2.5)


CRACKLES = {
    # fine glitter (strobe)
    "crackle_1": dict(dur=1.2, rate=130, big_frac=0.05, big_g=0.6, thin=0.55, hp=2600, air_fc=11000, wet=0.22),
    # sparse "dragon eggs" (willow ends)
    "crackle_2": dict(dur=1.5, rate=48, big_frac=0.2, big_g=0.9, thin=0.7, hp=1800, air_fc=9000, wet=0.28),
}


# --------------------------------------------------------------------------- launch

def launch(seed: int, p: dict) -> np.ndarray:
    """Mortar: tube thump (low sine + lift-charge blast + hollow tube mode) then a short receding whoosh."""
    rng = np.random.default_rng(seed)
    t = _t(p["dur"])
    n = len(t)
    f = p["thump_f"] * (1.0 + 0.6 * np.exp(-t / 0.012))
    thump = np.sin(2 * np.pi * np.cumsum(f) / SR) * exp_env(t, 0.0015, p["thump_tau"])
    blast = dsp.lowpass(rng.standard_normal(n), p["blast_fc"], order=2) * exp_env(t, 0.001, p["blast_tau"])
    tube = dsp.svf(rng.standard_normal(n) * exp_env(t, 0.001, p["tube_tau"]), p["tube_f"], q=p["tube_q"], mode="bp")
    tw = np.maximum(t - 0.015, 0.0)
    wc = p["wh_lo"] + (p["wh_hi"] - p["wh_lo"]) * np.exp(-tw / (p["wh_tau"] * 1.5))
    whoosh = dsp.svf(rng.standard_normal(n), wc, q=1.2, mode="bp")
    whoosh *= (t >= 0.015) * np.clip(tw / 0.03, 0, 1) * np.exp(-tw / p["wh_tau"])
    dry = norm_peak(thump) + 0.7 * norm_peak(blast) + 0.5 * norm_peak(tube) + p["wh_g"] * norm_peak(whoosh)
    ir = dsp.make_ir(rng, t60_low=p["t60"], t60_high=p["t60"] * 0.35, predelay=0.03)
    wet = norm_peak(dsp.convolve(dsp.highpass(dry, 250.0), ir, n), p["wet"] * np.max(np.abs(dry)))
    out = dsp.highpass(dry + _echoes(dry, [(0.21, 0.15, 1500)]) + wet, 35.0, order=2)
    return _saturate(out, 1.7)


LAUNCHES = {
    "launch_1": dict(dur=1.4, thump_f=95, thump_tau=0.07, blast_fc=700, blast_tau=0.05, tube_f=240, tube_q=6,
                     tube_tau=0.09, wh_hi=3500, wh_lo=1800, wh_tau=0.22, wh_g=0.45, t60=1.2, wet=0.30),
    "launch_2": dict(dur=1.8, thump_f=75, thump_tau=0.10, blast_fc=500, blast_tau=0.07, tube_f=180, tube_q=5,
                     tube_tau=0.12, wh_hi=2800, wh_lo=1400, wh_tau=0.30, wh_g=0.50, t60=1.6, wet=0.35),
    "launch_3": dict(dur=1.0, thump_f=120, thump_tau=0.05, blast_fc=1000, blast_tau=0.035, tube_f=320, tube_q=7,
                     tube_tau=0.06, wh_hi=4500, wh_lo=2500, wh_tau=0.15, wh_g=0.40, t60=1.0, wet=0.25),
}


# --------------------------------------------------------------------------- whistle

def whistle(seed: int, dur: float = 2.2, f0: float = 850.0, f1: float = 2700.0) -> np.ndarray:
    """Rising whistle: breathy tone gliding up, slight wobble, receding as it climbs."""
    rng = np.random.default_rng(seed)
    t = _t(dur)
    n = len(t)
    rise = 1.95
    p = np.clip(t / rise, 0, 1) ** 0.85
    f = f0 * (f1 / f0) ** p
    f = f * (1.0 + 0.006 * dsp.smooth_noise(rng, n, 14.0)) * (1.0 + 0.004 * np.sin(2 * np.pi * 7.0 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(ph) + 0.28 * np.sin(2 * ph + 0.3) + 0.1 * np.sin(3 * ph + 1.1)
    breath = norm_peak(dsp.svf(rng.standard_normal(n), f, q=6.0, mode="bp"))
    sizzle = norm_peak(dsp.highpass(rng.standard_normal(n), 5000)) * 0.06
    env = np.clip(t / 0.07, 0, 1) * np.exp(-t / 2.8) * (0.85 + 0.15 * np.clip(dsp.smooth_noise(rng, n, 6.0), -1, 1))
    env *= np.clip((dur - t) / 0.3, 0, 1) ** 2
    dry = (norm_peak(tone) + 0.35 * breath + sizzle) * env
    dry = dsp.lowpass(dry, 7500, order=2)
    ir = dsp.make_ir(rng, t60_low=1.6, t60_high=0.9, predelay=0.03)
    wet = norm_peak(dsp.convolve(dry, ir, n + int(0.8 * SR)), 0.3 * np.max(np.abs(dry)))
    out = np.concatenate([dry, np.zeros(len(wet) - n)]) + wet
    return norm_peak(dsp.highpass(out, 300.0, order=2))


# --------------------------------------------------------------------------- fountain loop

def fountain_loop(seed: int, dur: float = 4.0) -> np.ndarray:
    """Gerb hiss built entirely with circular (FFT-domain) operations, so sample N-1 flows into
    sample 0 exactly: shaped noise, periodic slow modulation, wrapped sputters, circular reverb."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    freqs = np.fft.rfftfreq(n, 1 / SR)

    def periodic_noise(shape: np.ndarray) -> np.ndarray:
        x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * shape, n)
        return x / x.std()

    def butter_mag(f, fc, order, kind):
        r = (f / fc) ** (2 * order)
        return np.sqrt(1 / (1 + r)) if kind == "lp" else np.sqrt(r / (1 + r))

    hiss_shape = butter_mag(freqs, 1600, 2, "hp") * butter_mag(freqs, 9000, 2, "lp")
    hiss_shape *= 1 + 0.8 * np.exp(-0.5 * ((freqs - 4500) / 1500) ** 2)
    roar_shape = butter_mag(freqs, 380, 3, "lp") * butter_mag(freqs, 60, 2, "hp")
    hiss = periodic_noise(hiss_shape)
    roar = periodic_noise(roar_shape)

    def periodic_mod(cycles, depths):
        m = np.zeros(n)
        for c, d in zip(cycles, depths):
            m += d * np.sin(2 * np.pi * c * t / dur + rng.uniform(0, 2 * np.pi))
        return m

    hiss *= 1 + periodic_mod([2, 3, 5, 9, 13], [0.05, 0.06, 0.04, 0.05, 0.03]) + periodic_mod(
        [47, 71, 103], [0.04, 0.03, 0.03])
    roar *= 1 + periodic_mod([1, 4, 7], [0.10, 0.08, 0.06])

    sput = np.zeros(n)
    for when in rng.uniform(0, dur, 70):
        length = rng.uniform(0.0008, 0.003)
        m = int(length * SR * 3)
        g = rng.standard_normal(m) * np.exp(-np.arange(m) / (length * SR / 2.5)) * rng.lognormal(0, 0.5)
        i = int(when * SR)
        idx = (np.arange(m) + i) % n
        np.add.at(sput, idx, g)
    sput = np.fft.irfft(np.fft.rfft(sput) * butter_mag(freqs, 2000, 2, "hp"), n)

    dry = hiss + 0.28 * roar + 0.9 * norm_peak(sput) * hiss.std() * 4
    ir = dsp.make_ir(rng, t60_low=1.2, t60_high=0.6, predelay=0.02)
    wet = dsp.circular_convolve(dry, ir)
    out = dry + 0.35 * wet * dry.std() / wet.std()
    return norm_peak(out - out.mean())


# --------------------------------------------------------------------------- main

def seam_report(x: np.ndarray) -> dict:
    """Loop seam check: jump N-1 -> 0 compared to ordinary adjacent-sample steps."""
    d = np.abs(np.diff(x))
    jump = abs(x[0] - x[-1])
    return {"seam_jump": round(float(jump), 5), "p99_step": round(float(np.percentile(d, 99)), 5),
            "seam_percentile": round(float(np.mean(d < jump) * 100), 1)}


def build_all() -> tuple[dict, dict]:
    sounds: dict[str, tuple[np.ndarray, str]] = {}
    for i, (name, p) in enumerate(BOOMS.items()):
        sounds[name] = (boom(100 + i, p), "boom")
    for i, (name, p) in enumerate(POPS.items()):
        sounds[name] = (pop(200 + i, p), "pop")
    for i, (name, p) in enumerate(CRACKLES.items()):
        sounds[name] = (crackle(300 + i, p), "crackle")
    for i, (name, p) in enumerate(LAUNCHES.items()):
        sounds[name] = (launch(400 + i, p), "launch")
    sounds["whistle_1"] = (whistle(500), "whistle")
    loop = fountain_loop(600)
    sounds["fountain_loop"] = (loop, "fountain")

    stats = {}
    out = {}
    for name, (x, cat) in sounds.items():
        if cat != "fountain":  # one-shots: trim leading/trailing silence
            x = dsp.trim_tail(dsp.trim_head(x), floor_db=-60.0)
        s = dsp.write_wav16(OUT_DIR / f"{name}.wav", x, PEAK_DB[cat])
        s["category"] = cat
        if cat == "fountain":
            s.update(seam_report(x))
        stats[name] = s
        out[name] = x
    return stats, out


def plot(sounds: dict, path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from scipy import signal
    except ImportError:
        print("matplotlib not installed; skipping plot")
        return
    names = list(sounds)
    fig, axes = plt.subplots(len(names), 2, figsize=(13, 1.55 * len(names)),
                             gridspec_kw={"width_ratios": [1, 2.2]}, constrained_layout=True)
    for row, name in enumerate(names):
        x = sounds[name]
        t = np.arange(len(x)) / SR
        ax = axes[row, 0]
        ax.plot(t, x, lw=0.4, color="#2a78d6")
        ax.set_ylim(-1.05, 1.05)
        ax.set_ylabel(name, rotation=0, ha="right", va="center", fontsize=9)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.2, lw=0.5)
        ax = axes[row, 1]
        f, tt, S = signal.spectrogram(x, SR, nperseg=1024, noverlap=896)
        ax.pcolormesh(tt, f, 10 * np.log10(S + 1e-12), shading="auto", cmap="magma", vmin=-110, vmax=-30)
        ax.set_yscale("symlog", linthresh=200)
        ax.set_ylim(30, 16000)
        ax.tick_params(labelsize=7)
    axes[-1, 0].set_xlabel("seconds (waveform, normalised)")
    axes[-1, 1].set_xlabel("seconds (spectrogram, Hz on log scale)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=80)
    print(f"wrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plot", action="store_true", help="also write Tools/out/sfx_spectrograms.png")
    args = ap.parse_args()
    stats, sounds = build_all()
    print(f"{'file':<20}{'sec':>7}{'peak dBFS':>11}{'rms dBFS':>10}  clip")
    for s in stats.values():
        extra = f"  seam jump {s['seam_jump']} vs p99 step {s['p99_step']}" if "seam_jump" in s else ""
        print(f"{s['file']:<20}{s['seconds']:>7.2f}{s['peak_dbfs']:>11.2f}{s['rms_dbfs']:>10.1f}  {s['clipped_samples']}{extra}")
    TOOLS_OUT.mkdir(parents=True, exist_ok=True)
    (TOOLS_OUT / "sfx_stats.json").write_text(json.dumps(stats, indent=2))
    if args.plot:
        plot({k: norm_peak(v) for k, v in sounds.items()}, TOOLS_OUT / "sfx_spectrograms.png")


if __name__ == "__main__":
    main()
