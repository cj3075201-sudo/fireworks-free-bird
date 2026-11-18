"""Offline re-creation of the Unity SFX bus (ShowController.BuildSounds + ShowAudio voice stealing)
to compare its loudness with the music, section by section (BS.1770 momentary loudness).

Usage (repo root):  python3 Tools/mix_check.py . [boom=0.12 pop=0.14 ... hit=1.6 music=0.85 master=1.0]
Defaults mirror ShowAudio's per-kind gains and ShowController.musicVolume; change both together.
Needs ffmpeg on PATH, numpy, scipy. Pitch and stereo pan are ignored (mono SFX at centre)."""
import json, subprocess, sys, wave
import numpy as np
from scipy.signal import lfilter

ROOT = sys.argv[1]
GAINS = {"boom": 0.12, "pop": 0.14, "crackle": 0.17, "launch": 0.065, "whistle": 0.14, "loop": 0.14}
MASTER = 1.0
MUSIC_VOL = 0.85
HIT = 1.6
for a in sys.argv[2:]:
    k, v = a.split("=")
    if k == "master": MASTER = float(v)
    elif k == "music": MUSIC_VOL = float(v)
    elif k == "hit": HIT = float(v)
    else: GAINS[k] = float(v)

SR = 44100
VOICES = {"boom": 6, "pop": 6, "crackle": 3, "launch": 5, "whistle": 3, "loop": 3}

def load_wav(p):
    with wave.open(p) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() == 2: x = x.reshape(-1, 2).mean(1)
    return x

sfx_dir = f"{ROOT}/Assets/Fireworks/Audio/SFX"
clips = {
    "boom": [load_wav(f"{sfx_dir}/boom_{i}.wav") for i in range(1, 5)],
    "pop": [load_wav(f"{sfx_dir}/pop_{i}.wav") for i in range(1, 4)],
    "crackle": [load_wav(f"{sfx_dir}/crackle_{i}.wav") for i in range(1, 3)],
    "launch": [load_wav(f"{sfx_dir}/launch_{i}.wav") for i in range(1, 4)],
    "whistle": [load_wav(f"{sfx_dir}/whistle_1.wav")],
    "loop": [load_wav(f"{sfx_dir}/fountain_loop.wav")],
}

show = json.load(open(f"{ROOT}/Assets/Fireworks/Show/cues.json"))
music_path = f"{ROOT}/Assets/Fireworks/Audio/Music/{show['music']}"
raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", music_path, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                     capture_output=True, check=True).stdout
music = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2) * MUSIC_VOL
N = len(music)

rng = np.random.default_rng(7)
events = []  # (t, kind, vol, dur or None)
for c in show["cues"]:
    s = c.get("scale", 1.0); typ = c["type"]
    loud = 0.6 + 0.4 * np.clip((s - 0.6) / 0.4, 0, 1)
    if typ == "fountain":
        events.append((c["t"], "loop", loud, c.get("dur", 4.0))); continue
    h = c["h"]; rise = 0.75 + 0.0185 * h
    if typ == "comet": rise *= 1.25
    launch = c["t"] - rise
    events.append((launch, "launch", loud, None))
    if typ == "comet" or (h >= 85 and rng.random() < 0.2): events.append((launch + 0.05, "whistle", loud, None))
    if typ == "comet": continue
    small = typ == "ring" or s < 1.0
    events.append((c["t"], "pop" if small else "boom", loud * (HIT if c.get("hit") else 1.0), None))
    if typ == "crossette":
        for k in range(3): events.append((c["t"] + 1.0 + k * 0.07, "pop", loud, None))
    elif typ == "strobe": events.append((c["t"] + 0.3, "crackle", loud, None))
    elif typ == "willow": events.append((c["t"] + 2.0, "crackle", loud, None))
events.sort(key=lambda e: e[0])

# Voice stealing: per kind, a new sound on a full kind cuts the oldest one.
bus = np.zeros(N, dtype=np.float32)
active = {k: [] for k in VOICES}  # list of [start_idx, end_idx, clip, vol]
placed = []
for t, kind, vol, dur in events:
    if t < 0: t = 0.0
    i0 = int(t * SR)
    if i0 >= N: continue
    clip = clips[kind][rng.integers(len(clips[kind]))]
    if kind == "loop":
        n = int(dur * SR); reps = int(np.ceil(n / len(clip))); clip = np.tile(clip, reps)[:n]
    v = active[kind]
    v[:] = [a for a in v if a[1] > i0]
    if len(v) >= VOICES[kind]:
        oldest = min(v, key=lambda a: a[0]); oldest[1] = i0; v.remove(oldest)
    rec = [i0, i0 + len(clip), clip, min(1.0, vol * GAINS[kind] * MASTER), kind]
    v.append(rec); placed.append(rec)
for i0, i1, clip, g, _k in placed:
    i1 = min(i1, N, i0 + len(clip))
    if i1 > i0: bus[i0:i1] += g * clip[: i1 - i0]

# Mono SFX at pan≈0 goes equally to both channels.
sfx_st = np.stack([bus, bus], 1)

def biquad_shelf_hp(fs):
    # K-weighting (BS.1770) built for any fs, as in pyloudnorm.
    G, Q, fc = 4.0, 1 / np.sqrt(2), 1500.0
    A = 10 ** (G / 40); w0 = 2 * np.pi * fc / fs; alpha = np.sin(w0) / (2 * Q)
    b1 = [A * ((A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha), -2 * A * ((A - 1) + (A + 1) * np.cos(w0)),
          A * ((A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)]
    a1 = [(A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha, 2 * ((A - 1) - (A + 1) * np.cos(w0)),
          (A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha]
    Q, fc = 0.5, 38.0; w0 = 2 * np.pi * fc / fs; alpha = np.sin(w0) / (2 * Q)
    b2 = [(1 + np.cos(w0)) / 2, -(1 + np.cos(w0)), (1 + np.cos(w0)) / 2]
    a2 = [1 + alpha, -2 * np.cos(w0), 1 - alpha]
    return (b1, a1), (b2, a2)

(b1, a1), (b2, a2) = biquad_shelf_hp(SR)
def kw(x):
    return lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)

def momentary(x):
    y = kw(x); p = (y ** 2).sum(1)
    win, hop = int(0.4 * SR), int(0.1 * SR)
    cs = np.concatenate([[0], np.cumsum(p)])
    idx = np.arange(0, len(p) - win, hop)
    ms = (cs[idx + win] - cs[idx]) / win
    return idx / SR, -0.691 + 10 * np.log10(ms + 1e-12)

tm, Lm = momentary(music)
_, Ls = momentary(sfx_st)
mix = music + sfx_st
_, Lx = momentary(mix)

def integ(L):
    L = L[L > -70]; rel = 10 * np.log10(np.mean(10 ** (L / 10))) - 10
    L = L[L > rel]; return 10 * np.log10(np.mean(10 ** (L / 10)))

print(f"gains master={MASTER} music={MUSIC_VOL} {GAINS}")
print(f"integrated  music {integ(Lm):6.1f} LUFS | sfx {integ(Ls):6.1f} | mix {integ(Lx):6.1f}")
print(f"mix sample peak {20*np.log10(np.abs(mix).max()):+.1f} dBFS, samples over 0 dBFS: {(np.abs(mix) > 1).sum()}")
secs = [(s["name"], s["start"], s["end"]) for s in show["sections"]]
drop = show.get("drop"); drop_end = show.get("drop_end")
rows = [("eruption", drop, drop + 4.0)] if drop else []
print(f"{'part':10s} {'start':>6s} {'end':>6s} | music  sfx med  sfx p90  (sfx-music) med  p90  | mix rise")
for name, a, b in rows + secs:
    m = (tm >= a) & (tm < b)
    if m.sum() == 0: continue
    d = Ls[m] - Lm[m]
    rise = 10 * np.log10(np.mean(10 ** (Lx[m] / 10))) - 10 * np.log10(np.mean(10 ** (Lm[m] / 10)))
    print(f"{name:10s} {a:6.1f} {b:6.1f} | {np.median(Lm[m]):5.1f} {np.median(Ls[m]):6.1f} {np.percentile(Ls[m],90):7.1f}"
          f"   {np.median(d):+6.1f} {np.percentile(d,90):+6.1f}  | {rise:+5.1f} dB")

a = int(drop * SR) if drop else 0
b = int(show["sections"][-1]["start"] * SR) if drop else N
print(f"\nper-kind integrated loudness ({a/SR:.0f}-{b/SR:.0f} s):")
for kind in VOICES:
    xs = np.zeros(N, dtype=np.float32)
    for rec in placed:
        if rec[4] != kind: continue
        i0, i1, clip, g = rec[:4]; i1 = min(i1, N, i0 + len(clip))
        if i1 > i0: xs[i0:i1] += g * clip[: i1 - i0]
    _, L = momentary(np.stack([xs[a:b], xs[a:b]], 1))
    print(f"  {kind:8s} {integ(L):6.1f} LUFS")
