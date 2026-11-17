"""Audio loading for the analysis tools: WAV via scipy, everything else via ffmpeg."""
from __future__ import annotations

import os
import shutil
import subprocess
from math import gcd
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile


def find_ffmpeg() -> str | None:
    for cand in (os.environ.get("FFMPEG"), shutil.which("ffmpeg"), str(Path.home() / ".local/bin/ffmpeg")):
        if cand and Path(cand).exists():
            return cand
    return None


def _wav(path: Path) -> tuple[np.ndarray, int]:
    sr, x = wavfile.read(str(path))
    if x.dtype.kind == "i":
        x = x.astype(np.float64) / float(np.iinfo(x.dtype).max + 1)
    elif x.dtype.kind == "u":
        x = (x.astype(np.float64) - 128.0) / 128.0
    else:
        x = x.astype(np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    return x, int(sr)


def _ffmpeg(path: Path, sr: int) -> np.ndarray:
    exe = find_ffmpeg()
    if exe is None:
        raise RuntimeError(f"ffmpeg not found; needed to decode {path.suffix} (set FFMPEG=/path/to/ffmpeg)")
    cmd = [exe, "-v", "error", "-nostdin", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def load_mono(path: str | Path, sr: int = 22050) -> tuple[np.ndarray, int, float]:
    """Return (mono signal at `sr`, sr, duration in seconds of the original file)."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            x, sr0 = _wav(path)
            duration = len(x) / sr0
            if sr0 != sr:
                g = gcd(sr0, sr)
                x = signal.resample_poly(x, sr // g, sr0 // g)
            return x, sr, duration
        except ValueError:
            pass  # exotic WAV flavour: fall through to ffmpeg
    x = _ffmpeg(path, sr)
    return x, sr, len(x) / sr
