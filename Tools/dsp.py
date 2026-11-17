"""Shared offline DSP helpers for the synthesis tools (SFX and the test song).

Everything here is deterministic given a numpy Generator, so the same seed
always produces bit-identical WAV files.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 44100


def db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def lin_to_db(x: float) -> float:
    return float(20.0 * np.log10(max(float(x), 1e-12)))


# --------------------------------------------------------------------------- filters

def _sos(kind: str, cutoff, order: int, sr: int):
    return signal.butter(order, cutoff, btype=kind, fs=sr, output="sos")


def lowpass(x, fc, order=2, sr=SR):
    return signal.sosfilt(_sos("lowpass", min(fc, sr * 0.45), order, sr), x)


def highpass(x, fc, order=2, sr=SR):
    return signal.sosfilt(_sos("highpass", fc, order, sr), x)


def bandpass(x, lo, hi, order=2, sr=SR):
    return signal.sosfilt(_sos("bandpass", [lo, min(hi, sr * 0.45)], order, sr), x)


def svf(x, fc, q=0.707, mode="lp", sr=SR):
    """Time-varying TPT state-variable filter (Zavalishin). `fc` may be an array."""
    x = np.asarray(x, dtype=float)
    fc = np.broadcast_to(np.asarray(fc, dtype=float), x.shape)
    g = np.tan(np.pi * np.clip(fc, 10.0, sr * 0.45) / sr)
    k = 1.0 / q
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    xs, A1, A2, A3 = x.tolist(), a1.tolist(), a2.tolist(), a3.tolist()
    lp = [0.0] * len(xs)
    bp = [0.0] * len(xs)
    ic1 = ic2 = 0.0
    for n in range(len(xs)):
        v3 = xs[n] - ic2
        v1 = A1[n] * ic1 + A2[n] * v3
        v2 = ic2 + A2[n] * ic1 + A3[n] * v3
        ic1 = 2.0 * v1 - ic1
        ic2 = 2.0 * v2 - ic2
        lp[n] = v2
        bp[n] = v1
    lp = np.asarray(lp)
    bp = np.asarray(bp)
    if mode == "lp":
        return lp
    if mode == "bp":
        return bp
    if mode == "hp":
        return x - k * bp - lp
    raise ValueError(mode)


# --------------------------------------------------------------------------- signals

def smooth_noise(rng: np.random.Generator, n: int, rate_hz: float, sr=SR) -> np.ndarray:
    """Zero-mean, unit-std noise band-limited to ~rate_hz (for slow modulation)."""
    pad = int(sr / max(rate_hz, 0.5))
    z = lowpass(rng.standard_normal(n + pad), rate_hz, order=2, sr=sr)[pad:]
    s = z.std()
    return z / s if s > 0 else z


def norm_peak(x: np.ndarray, peak: float = 1.0) -> np.ndarray:
    m = np.max(np.abs(x))
    return x * (peak / m) if m > 0 else x


def exp_env(t: np.ndarray, attack: float, tau: float) -> np.ndarray:
    """Linear attack then exponential decay (time constant tau, seconds)."""
    a = np.clip(t / attack, 0.0, 1.0) if attack > 0 else np.ones_like(t)
    return a * np.exp(-np.maximum(t - attack, 0.0) / tau)


def make_ir(
    rng: np.random.Generator,
    t60_low: float,
    t60_high: float,
    predelay: float = 0.02,
    split_hz: float = 900.0,
    early: list[tuple[float, float]] | None = None,
    sr=SR,
) -> np.ndarray:
    """Synthetic diffuse impulse response: decaying noise with a darker, longer low band.

    Normalised to unit energy so `wet` gains are comparable between calls.
    """
    length = max(t60_low, t60_high) * 1.1 + predelay
    n = int(length * sr)
    t = np.arange(n) / sr
    nz = rng.standard_normal(n)
    low = lowpass(nz, split_hz, order=2, sr=sr) * 10.0 ** (-3.0 * t / t60_low)
    high = highpass(nz, split_hz, order=2, sr=sr) * 10.0 ** (-3.0 * t / t60_high)
    ir = (low + 0.6 * high) * (1.0 - np.exp(-t / 0.012))
    d = int(predelay * sr)
    ir = np.concatenate([np.zeros(d), ir[: n - d]])
    peak = np.max(np.abs(ir))
    for when, gain in early or []:  # discrete reflections, gain relative to the tail peak
        i = int(when * sr)
        if i < n:
            ir[i] += gain * peak
    return ir / np.sqrt(np.sum(ir**2))


def convolve(x: np.ndarray, ir: np.ndarray, n_out: int | None = None) -> np.ndarray:
    y = signal.fftconvolve(x, ir)
    return y[: (n_out or len(y))]


def circular_convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Periodic convolution (keeps a loop seamless). `ir` is folded if longer than x."""
    n = len(x)
    h = np.zeros(n)
    for i in range(0, len(ir), n):
        seg = ir[i : i + n]
        h[: len(seg)] += seg
    return np.fft.irfft(np.fft.rfft(x) * np.fft.rfft(h), n)


def place(buf: np.ndarray, x: np.ndarray, t0: float, sr=SR) -> None:
    """Add `x` into `buf` starting at time t0 (clipped to the buffer)."""
    i = int(round(t0 * sr))
    if i >= len(buf):
        return
    if i < 0:
        x = x[-i:]
        i = 0
    m = min(len(x), len(buf) - i)
    buf[i : i + m] += x[:m]


# --------------------------------------------------------------------------- output

def trim_tail(x: np.ndarray, floor_db: float = -60.0, fade_s: float = 0.03, sr=SR) -> np.ndarray:
    """Cut trailing near-silence (relative to the peak) and fade the last bit to zero."""
    env = np.sqrt(signal.sosfiltfilt(_sos("lowpass", 30.0, 2, sr), x**2).clip(0))
    thr = np.max(np.abs(x)) * db_to_lin(floor_db)
    above = np.nonzero(env > thr)[0]
    end = int(above[-1]) + 1 if len(above) else len(x)
    y = x[:end].copy()
    f = min(int(fade_s * sr), len(y))
    y[len(y) - f :] *= np.linspace(1.0, 0.0, f) ** 2
    return y


def trim_head(x: np.ndarray, floor_db: float = -60.0) -> np.ndarray:
    thr = np.max(np.abs(x)) * db_to_lin(floor_db)
    idx = np.nonzero(np.abs(x) > thr)[0]
    return x[int(idx[0]) :] if len(idx) else x


def write_wav16(path: Path, x: np.ndarray, peak_db: float, sr=SR) -> dict:
    """Peak-normalise to `peak_db` dBFS and write 16-bit mono PCM. Returns stats."""
    x = np.asarray(x, dtype=float)
    x = norm_peak(x, db_to_lin(peak_db))
    q = np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sr, q)
    f = q.astype(float) / 32768.0
    peak = float(np.max(np.abs(f)))
    rms = float(np.sqrt(np.mean(f**2)))
    return {
        "file": path.name,
        "seconds": round(len(q) / sr, 3),
        "peak_dbfs": round(lin_to_db(peak), 2),
        "rms_dbfs": round(lin_to_db(rms), 1),
        "clipped_samples": int(np.sum(np.abs(q.astype(int)) >= 32767)),
    }
