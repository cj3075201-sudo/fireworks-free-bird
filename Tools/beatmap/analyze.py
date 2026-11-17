#!/usr/bin/env python3
"""Analyse a song for the firework show (numpy/scipy only, no librosa).

    python3 Tools/beatmap/analyze.py <audio> -o <analysis.json> [--bpm 120 | --bpm 60,140] [--tempo-split 180]

Output: duration, BPM, beat times, downbeats (bar starts, 4/4 assumed), onset-strength and
RMS curves (10 Hz), section boundaries snapped to bars with per-section energy (0-1), and
strong-hit times.

Method
  onset   : log-mel spectral flux (superflux-style max filter), 1024/256 STFT at 22.05 kHz
  tempo   : autocorrelation of the onset envelope x log-normal prior around 120 BPM
  beats   : Ellis (2007) dynamic-programming beat tracker; with a tempo change (found on a 12 s local
            tempo curve, or given with --tempo-split) one DP state per tempo, so the beat list runs on
            continuously across the change
  bars    : phase (0-3) whose beats show most chord change + low-band onset + timbre change
  sections: Foote checkerboard novelty on bar-synchronous features, multi-scale, peak-picked
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import ndimage, signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_io import load_mono  # noqa: E402

SR = 22050
N_FFT, HOP = 1024, 256  # onset / mel
FPS = SR / HOP
N_FFT_C, HOP_C = 4096, 1024  # chroma
N_MELS = 64
CURVE_HOP = 0.1
# Systematic timing bias, measured on synthetic kick/snare tracks at 84/97/141 BPM with
# known offsets (not on the test song): beats came out 6 ms early, impacts 4 ms late.
BEAT_LATENCY = 0.006
HIT_LATENCY = -0.004
# If the onsets halfway between tracked beats are at least this strong relative to the
# beats themselves, the tracker locked to half tempo: track again at double tempo.
# 0.42 separates a 141 BPM backbeat track tracked at 70.5 (0.46) from a true 68 BPM ballad with
# 8th-note hats (0.37); --bpm overrides all of this when the tempo is known.
HALF_TEMPO_RATIO = 0.42
MAX_BPM_AFTER_DOUBLING = 185.0


# --------------------------------------------------------------------------- spectra

def _frames_power(y: np.ndarray, n_fft: int, hop: int):
    pad = n_fft // 2
    yp = np.pad(y, (pad, pad))
    win = signal.get_window("hann", n_fft, fftbins=True)
    view = sliding_window_view(yp, n_fft)[::hop]
    for a in range(0, len(view), 2048):
        yield (np.abs(np.fft.rfft(view[a : a + 2048] * win, axis=1)) ** 2).astype(np.float32)


def mel_filterbank(n_fft: int, n_mels: int, fmin: float, fmax: float) -> tuple[np.ndarray, np.ndarray]:
    def mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def imel(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    edges = imel(np.linspace(mel(fmin), mel(fmax), n_mels + 2))
    fb = np.zeros((n_mels, len(freqs)), dtype=np.float32)
    for i in range(n_mels):
        lo, c, hi = edges[i], edges[i + 1], edges[i + 2]
        up = (freqs - lo) / (c - lo)
        down = (hi - freqs) / (hi - c)
        fb[i] = np.maximum(0, np.minimum(up, down))
        if fb[i].sum() == 0:  # band narrower than a bin: take the nearest bin
            fb[i, np.argmin(np.abs(freqs - c))] = 1.0
        fb[i] /= fb[i].sum()
    return fb, edges[1:-1]


def chroma_filterbank(n_fft: int, fmin=80.0, fmax=5000.0) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    fb = np.zeros((12, len(freqs)), dtype=np.float32)
    ok = (freqs >= fmin) & (freqs <= fmax)
    midi = 69 + 12 * np.log2(freqs[ok] / 440.0)
    pc = np.round(midi).astype(int) % 12
    w = np.cos(np.pi * (midi - np.round(midi))) ** 2  # favour bins near a semitone centre
    idx = np.nonzero(ok)[0]
    fb[pc, idx] = w
    return fb


# --------------------------------------------------------------------------- tempo & beats

def estimate_tempo(oenv: np.ndarray, lo_bpm=50.0, hi_bpm=220.0, prior_bpm=120.0, prior_oct=1.0):
    x = oenv - ndimage.uniform_filter1d(oenv, int(FPS * 1.5))
    x = np.maximum(x, 0)
    x = x - x.mean()
    n = len(x)
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * n)) ** 2)[:n]
    ac /= ac[0] + 1e-12
    lags = np.arange(1, n)
    bpm = 60.0 * FPS / lags
    ok = (bpm >= lo_bpm) & (bpm <= hi_bpm)
    prior = np.exp(-0.5 * (np.log2(bpm / prior_bpm) / prior_oct) ** 2)
    score = np.where(ok, ac[1:] * prior, -np.inf)
    k = int(np.argmax(score)) + 1
    # parabolic refinement of the lag
    if 1 < k < n - 1:
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b + c
        k = k + (0.5 * (a - c) / den if den != 0 else 0.0)
    period = float(k)
    conf = float(np.max(score[np.isfinite(score)]))
    return 60.0 * FPS / period, period, conf


def track_beats(oenv: np.ndarray, period: float, tightness: float = 100.0) -> np.ndarray:
    """Ellis 2007 dynamic programming. Returns beat frame indices."""
    o = oenv / (oenv.std() + 1e-9)
    w = np.arange(-int(period), int(period) + 1)
    local = np.convolve(o, np.exp(-0.5 * (w * 32.0 / period) ** 2), mode="same")
    n = len(local)
    lo, hi = int(round(2 * period)), max(1, int(round(period / 2)))
    offs = np.arange(-lo, -hi + 1)
    tx = -tightness * np.log(-offs / period) ** 2
    cum = np.zeros(n)
    back = np.full(n, -1, dtype=int)
    thresh = 0.01 * local.max()
    first = True
    for i in range(n):
        idx = i + offs
        valid = idx >= 0
        if valid.any():
            cand = cum[idx[valid]] + tx[valid]
            j = int(np.argmax(cand))
            best = cand[j]
            prev = int(idx[valid][j])
        else:
            best, prev = 0.0, -1
        cum[i] = local[i] + best
        if first and local[i] < thresh:
            back[i] = -1
        else:
            back[i] = prev
            first = False
    # last beat: last local max of cum above half the median of local maxima
    peaks = np.nonzero((cum[1:-1] > cum[:-2]) & (cum[1:-1] >= cum[2:]))[0] + 1
    if len(peaks) == 0:
        return np.array([], dtype=int)
    med = np.median(cum[peaks])
    good = peaks[cum[peaks] >= 0.5 * med]
    i = int(good[-1]) if len(good) else int(peaks[-1])
    beats = []
    while i >= 0:
        beats.append(i)
        i = int(back[i])
    return np.array(beats[::-1], dtype=int)


def refine_peak(curve: np.ndarray, i: int) -> float:
    if 0 < i < len(curve) - 1:
        a, b, c = curve[i - 1], curve[i], curve[i + 1]
        den = a - 2 * b + c
        if den < 0:
            return i + float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return float(i)


# --------------------------------------------------------------------------- tempo changes

TEMPO_WIN_S = 12.0  # local tempo window
TEMPO_HOP_S = 2.0
TEMPO_MIN_SEG_S = 30.0  # shortest tempo segment the auto detector will create
TEMPO_AGREE = 0.05  # two tempi "agree" within 5 % (octave multiples count as agreeing)
TEMPO_ZONE_AUTO_S = 10.0  # search radius for the exact switch around an auto-detected split
TEMPO_ZONE_MANUAL_S = 3.0  # ... and within +- this of a --tempo-split time
TEMPO_ZONE_FINE_S = 1.5  # after refine_split() the switch must happen within +- this


def local_tempi(oenv: np.ndarray) -> np.ndarray:
    """(centre time, bpm, confidence) for overlapping 12 s windows."""
    W, H = int(TEMPO_WIN_S * FPS), int(TEMPO_HOP_S * FPS)
    out = []
    for st in range(0, max(1, len(oenv) - W + 1), H):
        bpm, _, conf = estimate_tempo(oenv[st : st + W])
        out.append(((st + W / 2) / FPS, bpm, conf))
    return np.array(out)


def _agree_matrix(bpms: np.ndarray) -> np.ndarray:
    d = np.abs(np.log2(bpms[:, None] / bpms[None, :]))
    d = np.minimum(d, np.abs(d - 1.0))  # octave multiples agree
    return d < np.log2(1 + TEMPO_AGREE)


def detect_tempo_splits(lt: np.ndarray, max_splits: int = 3) -> list[float]:
    """Binary segmentation of the local tempo curve: split where one steady tempo turns into
    another clearly different (not octave-related) steady tempo."""
    if len(lt) < 10:
        return []
    tc, bpm, conf = lt[:, 0], lt[:, 1], lt[:, 2]
    valid = conf >= max(0.08, 0.4 * np.median(conf))
    A = _agree_matrix(bpm) & valid[None, :]

    def fit(i, j):
        """(misfit count, representative bpm, valid count) for windows [i, j)."""
        cand = np.nonzero(valid[i:j])[0] + i
        if len(cand) == 0:
            return 0, None, 0
        counts = A[np.ix_(cand, np.arange(i, j))].sum(axis=1)
        k = int(np.argmax(counts))
        return len(cand) - int(counts[k]), float(bpm[cand[k]]), len(cand)

    splits: list[float] = []

    def recurse(i, j, depth):
        if depth >= max_splits:
            return
        base, rep, nv = fit(i, j)
        if nv < 10 or rep is None:
            return
        best = None
        for k in range(i + 1, j):
            if tc[k - 1] - tc[i] < TEMPO_MIN_SEG_S - TEMPO_WIN_S or tc[j - 1] - tc[k] < TEMPO_MIN_SEG_S - TEMPO_WIN_S:
                continue
            cl, rl, nl = fit(i, k)
            cr, rr, nr = fit(k, j)
            if rl is None or rr is None or nl < 5 or nr < 5:
                continue
            if _agree_matrix(np.array([rl, rr]))[0, 1]:
                continue
            if cl > 0.5 * nl or cr > 0.5 * nr:
                continue
            if best is None or cl + cr < best[0]:
                best = (cl + cr, k)
        if best is None or base - best[0] < max(5, 0.2 * nv):
            return
        k = best[1]
        splits.append(float((tc[k - 1] + tc[k]) / 2))
        recurse(i, k, depth + 1)
        recurse(k, j, depth + 1)

    recurse(0, len(lt), 0)
    return sorted(splits)


def period_curve(lt: np.ndarray, rep_period: float, n: int, a_f: int, b_f: int) -> np.ndarray:
    """Per-frame beat period that follows slow tempo drift (an accelerating solo) around one tempo
    segment's representative period: local tempi folded to its octave, within +-20 %, median-smoothed."""
    rep_bpm = 60.0 * FPS / rep_period
    tc, bpm, conf = lt[:, 0], lt[:, 1], lt[:, 2]
    valid = conf >= max(0.15, 0.6 * np.median(conf))  # only clearly periodic windows steer the tempo
    folded = bpm * 2.0 ** np.round(np.log2(rep_bpm / bpm))
    ok = valid & (np.abs(np.log(folded / rep_bpm)) < np.log(1.18))
    sel = ok & (tc >= a_f / FPS - TEMPO_WIN_S / 2) & (tc <= b_f / FPS + TEMPO_WIN_S / 2)
    if sel.sum() < 3:
        return np.full(n, rep_period)
    v = ndimage.median_filter(folded[sel], 3, mode="nearest")
    return 60.0 * FPS / np.interp(np.arange(n) / FPS, tc[sel], v)


def track_beats_multi(oenv: np.ndarray, periods: list[float], zones: list[tuple[int, int]],
                      tightness: float = 100.0, curves: list[np.ndarray] | None = None
                      ) -> tuple[np.ndarray, np.ndarray]:
    """Ellis DP with one state per tempo segment, visited in order. State k may only run inside
    its own frames; inside zones[k] it may hand over to state k+1 once (no going back).
    Returns (beat frames, state of each beat)."""
    K, n = len(periods), len(oenv)
    o = oenv / (oenv.std() + 1e-9)
    loc = []
    for P in periods:
        w = np.arange(-int(P), int(P) + 1)
        loc.append(np.convolve(o, np.exp(-0.5 * (w * 32.0 / P) ** 2), mode="same"))
    lo_k = [0] + [z[0] for z in zones]
    hi_k = [z[1] for z in zones] + [n]
    same = []
    for P in periods:
        offs = np.arange(-int(round(2 * P)), -max(1, int(round(P / 2))) + 1)
        same.append((offs, -tightness * np.log(-offs / P) ** 2))
    cross = [None]
    for k in range(1, K):
        P0, P1 = periods[k - 1], periods[k]
        offs = np.arange(-int(round(2 * max(P0, P1))), -max(1, int(round(min(P0, P1) / 2))) + 1)
        d = -offs.astype(float)
        cost = np.minimum(np.log(d / P0) ** 2, np.log(d / P1) ** 2)
        cross.append((offs, -tightness * cost))
    cum = np.full((K, n), -np.inf)
    back = np.full((K, n, 2), -1, dtype=int)
    thresh = 0.01 * max(lc.max() for lc in loc)
    first = True
    for i in range(n):
        for k in range(K):
            if not (lo_k[k] <= i < hi_k[k]):
                continue
            best, bs, bf = -np.inf, -1, -1
            if curves is not None:  # tempo that drifts within the segment
                Pi = float(curves[k][i])
                offs = np.arange(-int(round(2 * Pi)), -max(1, int(round(Pi / 2))) + 1)
                tx = -tightness * np.log(-offs / Pi) ** 2
            else:
                offs, tx = same[k]
            idx = i + offs
            ok = idx >= 0
            if ok.any():
                c = cum[k, idx[ok]] + tx[ok]
                j = int(np.argmax(c))
                if c[j] > best:
                    best, bs, bf = c[j], k, int(idx[ok][j])
            if k > 0 and i < hi_k[k - 1] + 1:
                offs, tx = cross[k]
                idx = i + offs
                ok = idx >= 0
                if ok.any():
                    c = cum[k - 1, idx[ok]] + tx[ok]
                    j = int(np.argmax(c))
                    if c[j] > best:
                        best, bs, bf = c[j], k - 1, int(idx[ok][j])
            if k == 0 and not np.isfinite(best):
                best = 0.0
            if not np.isfinite(best):
                continue
            cum[k, i] = loc[k][i] + best
            if k == 0 and first and loc[k][i] < thresh:
                back[k, i] = (-1, -1)
            else:
                back[k, i] = (bs, bf)
                if k == 0:
                    first = False
    last = cum[K - 1]
    rng_ = np.arange(lo_k[K - 1], n)
    seg = last[rng_]
    pk = rng_[1:-1][(seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]) & np.isfinite(seg[1:-1])]
    if len(pk) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    med = np.median(last[pk])
    good = pk[last[pk] >= 0.5 * med]
    i, k = (int(good[-1]) if len(good) else int(pk[-1])), K - 1
    frames, states = [], []
    while i >= 0 and k >= 0:
        frames.append(i)
        states.append(k)
        k, i = int(back[k, i, 0]), int(back[k, i, 1])
    return np.array(frames[::-1], dtype=int), np.array(states[::-1], dtype=int)


def _periodicity(x: np.ndarray, P: float) -> float:
    """How well x repeats at lag P (and 2P): normalised autocorrelation, detrended like estimate_tempo."""
    x = np.maximum(x - ndimage.uniform_filter1d(x, int(FPS * 1.5)), 0)
    x = x - x.mean()
    den = float(np.dot(x, x)) + 1e-12
    out = 0.0
    for lag in (P, 2 * P):
        L = int(round(lag))
        if 0 < L < len(x) - 1:
            out += float(np.dot(x[:-L], x[L:])) / den
    return out / 2


def refine_split(oenv: np.ndarray, t_split: float, P_old: float, P_new: float, search_s: float,
                 win_s: float = 6.0) -> float:
    """Exact-ish switch time: the cut where the audio before it repeats best at the old period
    and the audio after it at the new one."""
    W = int(win_s * FPS)
    c0 = int(t_split * FPS)
    best, best_c = -np.inf, c0
    for c in range(max(W, c0 - int(search_s * FPS)), min(len(oenv) - W, c0 + int(search_s * FPS)) + 1, 2):
        sc = _periodicity(oenv[c - W : c], P_old) + _periodicity(oenv[c : c + W], P_new)
        if sc > best:
            best, best_c = sc, c
    return best_c / FPS


def _tempo_for(oenv_seg: np.ndarray, hint: float | None):
    """Tempo (bpm, period frames) for one steady stretch, with the half-tempo check."""
    if hint:
        bpm0, period, conf = estimate_tempo(oenv_seg, hint * 0.92, hint * 1.08, prior_bpm=hint)
        return bpm0, period, conf
    bpm0, period, conf = estimate_tempo(oenv_seg)
    bf = track_beats(oenv_seg, period)
    if len(bf) > 8 and 60.0 * FPS / (period / 2) <= MAX_BPM_AFTER_DOUBLING:
        mids = ((bf[1:] + bf[:-1]) // 2).astype(int)
        on_b = np.array([oenv_seg[max(0, f - 2) : f + 3].max() for f in bf])
        on_m = np.array([oenv_seg[max(0, f - 2) : f + 3].max() for f in mids])
        if np.median(on_m) >= HALF_TEMPO_RATIO * np.median(on_b):
            period /= 2
    return 60.0 * FPS / period, period, conf


def _bpm_from_beats(bt: np.ndarray) -> float:
    ibi = np.diff(bt)
    med = np.median(ibi)
    steady = np.abs(ibi - med) < 0.08 * med
    idx = np.arange(len(bt))
    if steady.mean() > 0.6 and steady.sum() >= 2:
        return float(60.0 / np.polyfit(idx[1:][steady], bt[1:][steady], 1)[0])
    return float(60.0 / med)


# --------------------------------------------------------------------------- structure

def zscore_cols(F: np.ndarray) -> np.ndarray:
    med = np.median(F, axis=0)
    mad = np.median(np.abs(F - med), axis=0) * 1.4826
    sd = np.where(mad > 1e-6, mad, F.std(axis=0) + 1e-6)
    return (F - med) / sd


def pairwise(F: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.maximum(((F[:, None, :] - F[None, :, :]) ** 2).sum(-1), 0)) / np.sqrt(F.shape[1])
    m = np.median(d[np.triu_indices(len(F), 1)]) if len(F) > 2 else 1.0
    return d / (m + 1e-9)


def foote_novelty(D: np.ndarray, half: int) -> np.ndarray:
    n = len(D)
    nov = np.zeros(n + 1)
    for b in range(1, n):
        L = min(half, b, n - b)
        if L < 1:
            continue
        A = slice(b - L, b)
        B = slice(b, b + L)
        cross = D[A, B].mean()
        within = 0.5 * (D[A, A].mean() + D[B, B].mean())
        # shrink novelty measured with a truncated kernel (edges are less certain)
        nov[b] = max(cross - within, 0.0) * (L / half) ** 0.5
    return nov


def pick_boundaries(nov: np.ndarray, n_bars: int, min_len: int, max_sections: int) -> list[int]:
    cands = []
    for b in range(2, n_bars - 1):
        lo, hi = max(0, b - 2), min(len(nov), b + 3)
        if nov[b] > 0 and nov[b] == nov[lo:hi].max():
            cands.append(b)
    if not cands:
        return []
    vals = np.array([nov[b] for b in cands])
    thr = max(0.3 * vals.max(), np.median(nov[1:n_bars]) + 1.5 * np.std(nov[1:n_bars]) * 0.5)
    chosen: list[int] = []
    for b in sorted(cands, key=lambda b: -nov[b]):
        if nov[b] < thr or len(chosen) >= max_sections - 1:
            break
        if all(abs(b - c) >= min_len for c in chosen) and b >= min_len // 2 and n_bars - b >= 2:
            chosen.append(b)
    return sorted(chosen)


def impact_curve(mel: np.ndarray, mel_f: np.ndarray, oenv: np.ndarray) -> np.ndarray:
    """How hard each frame lands: amplitude rise over the previous ~100 ms (full, low, high band)
    blended with the log-flux onset. A soft pad entering from silence scores low; a kick+crash
    on top of a loud section scores high."""
    amp = np.sqrt(np.maximum(mel, 0))
    out = np.zeros(mel.shape[1])
    for band, w in ((np.ones(len(mel_f), bool), 1.0), (mel_f < 150, 0.7), (mel_f > 4000, 0.5)):
        x = amp[band].sum(axis=0)
        prev = ndimage.uniform_filter1d(x, 10, origin=0)  # mean of frames t-6 .. t+3
        prev = np.concatenate([np.full(7, x[0]), prev[:-7]])  # shift so it covers ~t-12 .. t-3
        j = np.maximum(0.0, x - prev)
        out += w * np.minimum(j / (np.percentile(j, 99.5) + 1e-12), 2.0)
    # log-flux explodes when sound starts from silence, so it is capped
    return 0.7 * out / 2.2 + 0.3 * np.minimum(oenv / (np.percentile(oenv, 99.5) + 1e-12), 1.5)


# --------------------------------------------------------------------------- bar features

# Distance weight per feature group and the novelty scales (bars) each group takes part in.
# chroma only at >= 4 bars: chords change every bar, so 2-bar chroma means are mostly noise.
GROUP_WEIGHTS = {"shape": 1.0, "level": 1.0, "rhythm": 1.0, "density": 0.5, "chroma": 0.6}
GROUP_MIN_HALF = {"chroma": 4}
NOVELTY_SCALES = (2, 4, 8)
HIT_BONUS = 0.15


def _mean_cols(M: np.ndarray, tm: np.ndarray, a: float, b: float) -> np.ndarray:
    i0, i1 = np.searchsorted(tm, a), np.searchsorted(tm, b)
    if i1 <= i0:
        i1 = min(i0 + 1, M.shape[1])
        i0 = max(0, i1 - 1)
    return M[:, i0:i1].mean(axis=1)


def bar_features(bar_edges, times, L, rms_db, loud_top, oenv, low_env, high_env, chroma, ctimes) -> dict:
    """Per-bar feature groups (rows = bars), each column robust-z-scored across bars.

    shape  : 12-band log-mel profile minus its mean (timbre without loudness, so fades stay one section)
    level  : bar loudness in dB, floored 30 dB under the loudest part
    rhythm : 16-step onset pattern (full / low / high band) - groove vs. roll vs. no drums
    density: mean onset strength per band
    chroma : 12 pitch classes (harmony)
    """
    n_bars = len(bar_edges) - 1
    Lg = np.stack([L[g].mean(axis=0) for g in np.array_split(np.arange(L.shape[0]), 12)])
    envs = [e / (np.percentile(e, 99) + 1e-9) for e in (oenv, low_env, high_env)]
    out = {k: [] for k in GROUP_WEIGHTS}
    for i in range(n_bars):
        a, b = bar_edges[i], bar_edges[i + 1]
        prof = _mean_cols(Lg, times, a, b)
        out["shape"].append(prof - prof.mean())
        lvl = 10 * np.log10(_mean_cols(10 ** (rms_db[None] / 10), times, a, b)[0] + 1e-12)
        out["level"].append([max(lvl, loud_top - 30.0)])
        grid = a + (b - a) * np.arange(16) / 16
        fi = np.clip(np.searchsorted(times, grid), 1, len(times) - 2)
        out["rhythm"].append(np.concatenate([np.log1p(4 * np.maximum.reduce([e[fi - 1], e[fi], e[fi + 1]]))
                                             for e in envs]))
        out["density"].append([_mean_cols(e[None], times, a, b)[0] for e in envs])
        c = _mean_cols(chroma, ctimes, a, b)
        out["chroma"].append(c / (c.sum() + 1e-9))
    return {k: zscore_cols(np.array(v, dtype=float)) for k, v in out.items()}


def section_novelty(groups: dict) -> np.ndarray:
    n_bars = len(next(iter(groups.values())))
    dist = {k: pairwise(F) for k, F in groups.items()}
    nov = np.zeros(n_bars + 1)
    for half in NOVELTY_SCALES:
        D = sum(GROUP_WEIGHTS[k] * d for k, d in dist.items() if half >= GROUP_MIN_HALF.get(k, 0))
        nv = foote_novelty(D, half)
        nov += nv / (nv.max() + 1e-9)
    return nov / len(NOVELTY_SCALES)


# --------------------------------------------------------------------------- sections from bars

def bar_stats(bar_edges, times, rms_db, oenv, high_env, music_start, music_end) -> list[dict]:
    """Loudness / onset / brightness per bar (only frames inside the audible part of the song)."""
    out = []
    for i in range(len(bar_edges) - 1):
        a, b = float(bar_edges[i]), float(bar_edges[i + 1])
        m = (times >= max(a, music_start)) & (times < min(b, music_end + 0.01))
        if not m.any():
            m = (times >= a) & (times < b)
        n = int(m.sum())
        out.append({"t": round(a, 3), "end": round(b, 3), "n": n,
                    "power": float(np.mean(10 ** (rms_db[m] / 10))) if n else 1e-12,
                    "onset": round(float(oenv[m].mean()), 5) if n else 0.0,
                    "bright": round(float(high_env[m].mean()), 5) if n else 0.0})
    return out


def make_segments(bars: list[dict], seg_bars: list[int], duration: float, novelty=None) -> list[dict]:
    """Sections from bar indices [0, b1, ..., n_bars]: time span, loudness, slope and energy 0..1.
    Shared with choreograph.py --boundaries so hand-made sections get the same energy scale."""
    segments = []
    for k in range(len(seg_bars) - 1):
        idx = list(range(seg_bars[k], seg_bars[k + 1]))
        a = 0.0 if k == 0 else float(bars[seg_bars[k]]["t"])
        b = duration if k == len(seg_bars) - 2 else float(bars[seg_bars[k + 1]]["t"])
        w = np.array([max(bars[i]["n"], 1) for i in idx], dtype=float)
        pw = np.array([bars[i]["power"] for i in idx])
        bar_db = 10 * np.log10(pw + 1e-12)
        centres = np.array([(bars[i]["t"] + bars[i]["end"]) / 2 for i in idx])
        slope = float(np.polyfit(centres, bar_db, 1, w=w)[0]) if len(idx) >= 3 else (
            float((bar_db[-1] - bar_db[0]) / max(centres[-1] - centres[0], 1e-6)) if len(idx) == 2 else 0.0)
        segments.append({"start": round(a, 3), "end": round(b, 3), "bars": len(idx),
                         "db": 10 * np.log10(np.sum(w * pw) / w.sum() + 1e-12),
                         "onset": float(np.sum(w * [bars[i]["onset"] for i in idx]) / w.sum()),
                         "bright": float(np.sum(w * [bars[i]["bright"] for i in idx]) / w.sum()),
                         "slope_db_per_s": slope,
                         "novelty": round(float(novelty[seg_bars[k]]), 3) if (novelty is not None and k > 0) else None})
    # energy 0..1: loudness dominates, onset density and brightness refine
    dbs = np.array([s["db"] for s in segments])
    ons = np.array([s["onset"] for s in segments])
    brs = np.array([s["bright"] for s in segments])

    def rel(v, span=None):
        lo, hi = v.min(), v.max()
        if span is not None:
            lo = min(lo, hi - span)
        return (v - lo) / (hi - lo + 1e-9)

    if len(segments) > 1:
        e = np.clip(0.75 * rel(dbs, span=12.0) + 0.15 * rel(ons) + 0.10 * rel(brs), 0.05, 1.0)
    else:
        e = np.array([0.7])
    for seg, ei in zip(segments, e):
        seg["energy"] = round(float(ei), 3)
        seg["db"] = round(float(seg["db"]), 2)
        seg["onset"] = round(seg["onset"], 4)
        seg["bright"] = round(seg["bright"], 4)
        seg["slope_db_per_s"] = round(seg["slope_db_per_s"], 3)
    return segments


# --------------------------------------------------------------------------- main analysis

def analyze(path: Path, bpm_hint=None, tempo_splits: list[float] | None = None) -> dict:
    """bpm_hint: one tempo, or a list with one tempo per tempo segment.
    tempo_splits: times where the tempo changes (None = detect automatically)."""
    y, sr, duration = load_mono(path, SR)
    if len(y) < SR:
        raise ValueError("audio shorter than 1 s")

    fb, mel_f = mel_filterbank(N_FFT, N_MELS, 30.0, 11000.0)
    mel = np.concatenate([(P @ fb.T) for P in _frames_power(y, N_FFT, HOP)]).T  # (mels, T)
    T = mel.shape[1]
    times = np.arange(T) / FPS

    L = 10 * np.log10(mel + 1e-10)
    L = np.maximum(L, L.max() - 80.0)
    ref = ndimage.maximum_filter1d(L, size=3, axis=0)
    flux = np.zeros_like(L)
    flux[:, 1:] = np.maximum(0.0, L[:, 1:] - ref[:, :-1])
    oenv = flux.mean(axis=0)
    low_env = flux[mel_f < 150].mean(axis=0)
    high_env = flux[mel_f > 4000].mean(axis=0)

    # RMS (dB) per onset frame
    frames = sliding_window_view(np.pad(y, (N_FFT // 2, N_FFT // 2)), N_FFT)[::HOP][:T]
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms)
    loud_top = np.percentile(rms_db, 99)
    loud = np.clip((rms_db - (loud_top - 40.0)) / 40.0, 0.0, 1.0)  # 0..1 over a 40 dB range
    active = np.nonzero(rms_db > loud_top - 45.0)[0]
    music_start = times[active[0]] if len(active) else 0.0
    music_end = times[active[-1]] if len(active) else duration

    # tempo + beats (one tempo unless a change is found or given)
    hints = list(bpm_hint) if isinstance(bpm_hint, (list, tuple)) else ([bpm_hint] if bpm_hint else [])
    if tempo_splits is None:
        splits = detect_tempo_splits(local_tempi(oenv)) if len(hints) != 1 else []  # one --bpm = one tempo
        zone_s = TEMPO_ZONE_AUTO_S
    else:
        splits = sorted(float(x) for x in tempo_splits)
        zone_s = TEMPO_ZONE_MANUAL_S
    smooth = np.convolve(oenv, np.hanning(5) / np.hanning(5).sum(), mode="same")
    if not splits:
        bpm_hint = hints[0] if hints else None
        if bpm_hint:
            bpm0, period, tempo_conf = estimate_tempo(oenv, bpm_hint * 0.92, bpm_hint * 1.08, prior_bpm=bpm_hint)
        else:
            bpm0, period, tempo_conf = estimate_tempo(oenv)
        bframes = track_beats(oenv, period)
        if not bpm_hint and len(bframes) > 8 and 60.0 * FPS / (period / 2) <= MAX_BPM_AFTER_DOUBLING:
            mids = ((bframes[1:] + bframes[:-1]) // 2).astype(int)
            on_b = np.array([oenv[max(0, f - 2) : f + 3].max() for f in bframes])
            on_m = np.array([oenv[max(0, f - 2) : f + 3].max() for f in mids])
            if np.median(on_m) >= HALF_TEMPO_RATIO * np.median(on_b):
                period /= 2
                bframes = track_beats(oenv, period)
        bstates = np.zeros(len(bframes), dtype=int)
    else:
        edges_f = [0] + [int(x * FPS) for x in splits] + [T]
        zone_f = int(zone_s * FPS)
        periods, confs = [], []
        for k in range(len(edges_f) - 1):
            a_f = edges_f[k] + (zone_f if k > 0 else 0)
            b_f = edges_f[k + 1] - (zone_f if k < len(edges_f) - 2 else 0)
            if b_f - a_f < int(8 * FPS):
                a_f, b_f = edges_f[k], edges_f[k + 1]
            h = hints[k] if len(hints) == len(edges_f) - 1 else None
            _, P, c = _tempo_for(oenv[a_f:b_f], h)
            periods.append(P)
            confs.append(c)
        splits = [refine_split(oenv, x, periods[k], periods[k + 1], zone_s) for k, x in enumerate(splits)]
        nz = int(TEMPO_ZONE_FINE_S * FPS)
        zones = [(max(0, int(x * FPS) - nz), min(T, int(x * FPS) + nz)) for x in splits]
        lt_all = local_tempi(oenv)
        curves = [period_curve(lt_all, P, T, edges_f[k], edges_f[k + 1]) for k, P in enumerate(periods)]
        bframes, bstates = track_beats_multi(oenv, periods, zones, curves=curves)
        bpm0 = 60.0 * FPS / periods[int(np.argmax(np.diff(edges_f)))]
        tempo_conf = float(np.mean(confs))
    btimes = np.array([refine_peak(smooth, int(i)) for i in bframes]) / FPS + BEAT_LATENCY
    keep = (btimes >= music_start - 0.08) & (btimes <= music_end + 0.05)
    bframes, btimes, bstates = bframes[keep], btimes[keep], bstates[keep]
    if len(btimes) < 8:
        raise ValueError("too few beats found; is this music?")
    ibi = np.diff(btimes)
    med = np.median(ibi)
    # a beat sitting on both grids (the downbeat of the new part) belongs to the new tempo
    for k in range(1, int(bstates.max()) + 1 if len(bstates) else 1):
        first = int(np.argmax(bstates == k))
        p_new = float(np.median(np.diff(btimes[bstates == k]))) if np.sum(bstates == k) > 2 else None
        p_old = float(np.median(np.diff(btimes[bstates == k - 1]))) if np.sum(bstates == k - 1) > 2 else None
        while p_new and p_old and first > 1 and bstates[first - 1] == k - 1:
            gap = btimes[first] - btimes[first - 1]
            if abs(gap - p_new) < abs(gap - p_old) and abs(gap - p_new) < 0.15 * p_new:
                bstates[first - 1] = k
                first -= 1
            else:
                break
        while p_new and p_old and first + 2 < len(btimes) and bstates[first + 1] == k:
            gap = btimes[first + 1] - btimes[first]  # first beats of the new part still at the old pace
            if abs(gap - p_old) < abs(gap - p_new) and abs(gap - p_old) < 0.15 * p_old:
                bstates[first] = k - 1
                first += 1
            else:
                break
    # tempo segments: where the tracker actually switched state
    seg_start_idx = [0] + [int(i) for i in np.nonzero(np.diff(bstates))[0] + 1]
    tempo_segments = []
    for k, i0 in enumerate(seg_start_idx):
        i1 = seg_start_idx[k + 1] if k + 1 < len(seg_start_idx) else len(btimes)
        bt = btimes[i0:i1]
        tempo_segments.append({"start": 0.0 if k == 0 else round(float(btimes[i0]), 3),
                               "end": round(float(btimes[i1]), 3) if i1 < len(btimes) else round(duration, 3),
                               "bpm": round(_bpm_from_beats(bt), 2) if len(bt) > 2 else None,
                               "beats": int(len(bt))})
    if len(tempo_segments) == 1:
        idx = np.arange(len(btimes))
        steady = np.abs(ibi - med) < 0.08 * med
        if steady.mean() > 0.6:
            slope = np.polyfit(idx[1:][steady], btimes[1:][steady], 1)[0]
            bpm = 60.0 / slope
        else:
            bpm = 60.0 / med
        tempo_segments[0]["bpm"] = round(float(bpm), 2)
    else:  # representative tempo: the longest stretch
        longest = max(tempo_segments, key=lambda g: g["end"] - g["start"])
        bpm = longest["bpm"]

    # chroma (for bar phase and sections)
    cfb = chroma_filterbank(N_FFT_C)
    chroma = np.concatenate([(P @ cfb.T) for P in _frames_power(y, N_FFT_C, HOP_C)]).T  # (12, Tc)
    chroma = np.log1p(chroma / (np.percentile(chroma, 95) + 1e-12) * 10)
    ctimes = np.arange(chroma.shape[1]) * HOP_C / SR

    # beat-level features for the downbeat phase
    edges = np.concatenate([btimes, [btimes[-1] + med]])
    bchroma = np.array([_mean_cols(chroma, ctimes, edges[i], edges[i + 1]) for i in range(len(btimes))])
    bmel = np.array([_mean_cols(L, times, edges[i], edges[i + 1]) for i in range(len(btimes))])

    def unit(v):
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(n, 1e-9)

    ch_change = np.zeros(len(btimes))
    ch_change[1:] = 1 - np.sum(unit(bchroma[1:]) * unit(bchroma[:-1]), axis=1)
    mel_change = np.zeros(len(btimes))
    mel_change[1:] = np.linalg.norm(bmel[1:] - bmel[:-1], axis=1)
    low_at = np.array([low_env[max(0, f - 2) : f + 3].max() for f in bframes])
    on_at = np.array([oenv[max(0, f - 2) : f + 3].max() for f in bframes])

    def z(v):
        return (v - v.mean()) / (v.std() + 1e-9)

    if len(tempo_segments) == 1:
        feat = 1.5 * z(ch_change) + 1.0 * z(low_at) + 0.5 * z(mel_change) + 0.5 * z(on_at)
        phase_scores = [float(feat[k::4].mean()) for k in range(4)]
        phase = int(np.argmax(phase_scores))
        downbeats = btimes[phase::4]
    else:  # bar phase found separately in every tempo segment
        downbeats, phase_scores = [], []
        bounds = seg_start_idx + [len(btimes)]
        for k in range(len(seg_start_idx)):
            sl = slice(bounds[k], bounds[k + 1])
            if bounds[k + 1] - bounds[k] < 8:
                downbeats.extend(btimes[sl][::4])
                continue
            feat = 1.5 * z(ch_change[sl]) + 1.0 * z(low_at[sl]) + 0.5 * z(mel_change[sl]) + 0.5 * z(on_at[sl])
            sc = [float(feat[j::4].mean()) for j in range(4)]
            phase_scores.append([round(v, 3) for v in sc])
            downbeats.extend(btimes[sl][int(np.argmax(sc))::4])
        downbeats = np.array(downbeats)
        phase = None

    # strong hits: sudden rise in band amplitude (impact) blended with log-flux (attack)
    hit_env = impact_curve(mel, mel_f, oenv)
    pk, _ = signal.find_peaks(hit_env, distance=int(0.2 * FPS))
    if len(pk):
        thr = max(np.percentile(hit_env[pk], 80), 2.0 * np.median(hit_env))
        pk = pk[hit_env[pk] >= thr]
    hmax = hit_env[pk].max() if len(pk) else 1.0
    hits = [{"t": round(refine_peak(hit_env, int(p)) / FPS + HIT_LATENCY, 3),
             "strength": round(float(hit_env[p] / hmax), 3)} for p in pk]

    # bar-synchronous features -> sections
    bar_edges = list(downbeats)
    med_first = med if len(tempo_segments) == 1 else float(np.median(np.diff(btimes[:16])))
    med_last = med if len(tempo_segments) == 1 else float(np.median(np.diff(btimes[-16:])))
    if bar_edges[0] > music_start + 0.5 * 4 * med_first:  # partial bar before the first downbeat
        bar_edges = [max(0.0, music_start)] + bar_edges
    bar_edges.append(min(duration, bar_edges[-1] + 4 * med_last))
    bar_edges = np.array(bar_edges)
    n_bars = len(bar_edges) - 1
    groups = bar_features(bar_edges, times, L, rms_db, loud_top, oenv, low_env, high_env, chroma, ctimes)
    nov = section_novelty(groups)
    # impacts (crash + kick) on a downbeat mark section starts: small bonus
    for i, tb in enumerate(bar_edges[:-1]):
        near = [h["strength"] for h in hits if abs(h["t"] - tb) < 0.08]
        if near and i > 0:
            nov[i] += HIT_BONUS * max(near)
    max_sections = int(np.clip(duration / 12.0, 3, 16))
    if len(tempo_segments) == 1:
        bnds = pick_boundaries(nov, n_bars, min_len=4, max_sections=max_sections)
    else:  # pick within every tempo segment on its own scale (the tempo change would dwarf the rest)
        bnds = []
        cuts = [0] + [int(np.argmin(np.abs(bar_edges[:-1] - g["start"]))) for g in tempo_segments[1:]] + [n_bars]
        for k in range(len(cuts) - 1):
            a_, b_ = cuts[k], cuts[k + 1]
            if b_ - a_ < 6:
                continue
            sub = nov[a_ : b_ + 1].copy()
            sub[0] = 0.0
            sub[-1] = 0.0
            seg_dur = float(bar_edges[b_] - bar_edges[a_])
            bnds += [a_ + b for b in pick_boundaries(sub, b_ - a_, min_len=4,
                                                    max_sections=int(np.clip(seg_dur / 12.0, 2, 16)))]
    for g in tempo_segments[1:]:  # a tempo change always starts a section
        bi = int(np.argmin(np.abs(bar_edges[:-1] - g["start"])))
        bnds = sorted({b for b in bnds if abs(b - bi) >= 3} | {bi} - {0})
    seg_bars = [0] + bnds + [n_bars]

    bars = bar_stats(bar_edges, times, rms_db, oenv, high_env, music_start, music_end)
    segments = make_segments(bars, seg_bars, duration, nov)

    beat_strength = on_at / (np.percentile(on_at, 95) + 1e-9)

    # 10 Hz curves
    step = max(1, int(round(CURVE_HOP * FPS)))
    nC = int(np.ceil(T / step))
    on_curve = [float(oenv[i * step : (i + 1) * step].max()) for i in range(nC)]
    on_norm = np.percentile(on_curve, 99) + 1e-9
    rms_curve = [float(loud[i * step : (i + 1) * step].mean()) for i in range(nC)]

    return {
        "version": 1,
        "source": path.name,
        "duration": round(duration, 3),
        "sample_rate_analysis": SR,
        "bpm": round(float(bpm), 2),
        "bpm_autocorr": round(float(bpm0), 2),
        "tempo_confidence": round(tempo_conf, 3),
        "meter": 4,
        "tempo_segments": tempo_segments,
        "downbeat_phase": phase,
        "downbeat_phase_scores": [round(v, 3) for v in phase_scores] if phase is not None else phase_scores,
        "music_start": round(float(music_start), 3),
        "music_end": round(float(music_end), 3),
        "beats": [round(float(t), 3) for t in btimes],
        "beat_strength": [round(float(min(v, 1.5)), 3) for v in beat_strength],
        "downbeats": [round(float(t), 3) for t in downbeats],
        "bar_edges": [round(float(t), 3) for t in bar_edges],
        "bars": [dict(b, power=round(b["power"], 9)) for b in bars],
        "segments": segments,
        "boundaries": [s["start"] for s in segments[1:]],
        "hits": hits,
        "curve_hop": round(step / FPS, 4),
        "onset_curve": [round(min(v / on_norm, 1.0), 3) for v in on_curve],
        "rms_curve": [round(v, 3) for v in rms_curve],
        "novelty_per_bar": [round(float(v), 3) for v in nov],
    }


def parse_bpm(text):
    if not text:
        return None
    vals = [float(v) for v in str(text).split(",") if v.strip()]
    return vals[0] if len(vals) == 1 else vals


def parse_splits(text):
    if text is None:
        return None
    if str(text).strip().lower() in ("none", "off", ""):
        return []
    return [float(v) for v in str(text).split(",") if v.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--bpm", default=None,
                    help="known tempo (search within +-8%%); comma list = one per tempo segment, e.g. 60,140")
    ap.add_argument("--tempo-split", default=None,
                    help="times (s) where the tempo changes, comma-separated; 'none' disables auto detection")
    args = ap.parse_args()
    res = analyze(Path(args.audio), parse_bpm(args.bpm), parse_splits(args.tempo_split))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    if len(res["tempo_segments"]) > 1:
        print("tempo: " + " | ".join(f"{g['start']:.1f}-{g['end']:.1f}s {g['bpm']} BPM" for g in res["tempo_segments"]))
    print(f"{res['source']}: {res['duration']:.1f}s  {res['bpm']:.2f} BPM  {len(res['beats'])} beats  "
          f"{len(res['segments'])} sections  {len(res['hits'])} hits -> {out}")
    for s in res["segments"]:
        print(f"  {s['start']:7.2f}-{s['end']:7.2f}  bars {s['bars']:3d}  energy {s['energy']:.2f}  "
              f"{s['db']:6.1f} dB  slope {s['slope_db_per_s']:+.2f} dB/s")


if __name__ == "__main__":
    main()
