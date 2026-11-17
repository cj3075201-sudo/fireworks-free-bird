#!/usr/bin/env python3
"""Draw a show timeline PNG: loudness + section energy, burst rate, cues by type, launch positions.

    python3 Tools/beatmap/plot_timeline.py <analysis.json> <cues.json> -o <timeline.png>

Needs matplotlib (optional dependency); prints a note and exits 0 without it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract as C  # noqa: E402

INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#d9d8d4"
SERIES = "#2a78d6"
ACCENT = "#eb6834"


def plot(analysis: dict, show: dict, out: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping timeline plot")
        return False
    dur = show["duration"]
    cues = show["cues"]
    aerial = [c for c in cues if c["type"] != "fountain"]
    fig, axes = plt.subplots(4, 1, figsize=(15 if dur < 150 else 20, 10.5), sharex=True,
                             gridspec_kw={"height_ratios": [1.3, 1.0, 2.0, 1.6]}, constrained_layout=True)
    for ax in axes:
        ax.set_xlim(0, dur)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(colors=MUTED, labelsize=8)
        ax.grid(axis="x", color=GRID, lw=0.5)
        for i, s in enumerate(show["sections"]):
            if i % 2 == 0:
                ax.axvspan(s["start"], s["end"], color="#f1f0ec", zorder=0)
            ax.axvline(s["start"], color=GRID, lw=0.8, zorder=1)

    # 1) loudness + section energy (both 0..1)
    ax = axes[0]
    hop = analysis["curve_hop"]
    rms = np.array(analysis["rms_curve"])
    ax.plot(np.arange(len(rms)) * hop, rms, color=SERIES, lw=1.0, alpha=0.55, label="loudness (RMS, 0-1)")
    for s in show["sections"]:
        ax.plot([s["start"], s["end"]], [s["energy"]] * 2, color=INK, lw=2.0)
        wide = (s["end"] - s["start"]) / dur > 0.07
        ax.text((s["start"] + s["end"]) / 2, 1.04, f"{s['name']}  e={s['energy']:.2f}" if wide else s["name"],
                ha="center", va="bottom", fontsize=9 if wide else 7, color=INK)
    ax.plot([], [], color=INK, lw=2.0, label="section energy")
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("0-1", color=MUTED, fontsize=9)
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.set_title(f"{show['music']} - {len(cues)} cues, {analysis['bpm']:.1f} BPM"
                 + (f", style {show['style']}" if "style" in show else ""), loc="left", fontsize=11, color=INK)

    # 2) aerial bursts per 1 s window
    ax = axes[1]
    ts = np.array(sorted(c["t"] for c in aerial))
    grid = np.arange(0, dur, 0.05)
    rate = [np.sum((ts >= g) & (ts < g + C.RATE_WINDOW)) for g in grid]
    ax.step(grid, rate, where="post", color=SERIES, lw=1.2)
    peaks = [(show["drop"], show.get("drop_end", show["drop"] + C.RATE_WINDOW))] if "drop" in show else []
    caps = [C.cap_at(g, g + C.RATE_WINDOW, show["sections"], peaks) for g in grid]
    ax.plot(grid, caps, color=MUTED, lw=0.8, ls="--")
    ax.text(0.5, C.MAX_RATE + 0.3, f"cap {C.MAX_RATE}/s (finale {C.FINALE_MAX_RATE})", fontsize=8, color=MUTED)
    ax.set_ylabel("bursts in\nnext 1 s", color=MUTED, fontsize=9)
    ax.set_ylim(0, C.FINALE_MAX_RATE + 2)

    # 3) cues by type (rows); size = scale; ring = hit; bar = fountain
    ax = axes[2]
    order = C.TYPES
    for c in cues:
        y = order.index(c["type"])
        if c["type"] == "fountain":
            ax.plot([c["t"], c["t"] + c["dur"]], [y, y], color=SERIES, lw=4, solid_capstyle="butt", alpha=0.6)
            continue
        ax.scatter(c["t"], y, s=14 * c["scale"] ** 2, color=SERIES, alpha=0.75, lw=0)
        if c.get("hit"):
            ax.scatter(c["t"], y, s=110, facecolors="none", edgecolors=ACCENT, lw=1.2)
    ax.scatter([], [], s=110, facecolors="none", edgecolors=ACCENT, lw=1.2, label="hit (flash + shake)")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.set_yticks(range(len(order)), order, fontsize=8)
    ax.set_ylim(-0.7, len(order) - 0.3)

    if "drop" in show:
        for ax in axes:
            ax.axvline(show["drop"], color=ACCENT, lw=1.2, ls="--", zorder=3)
        axes[0].text(show["drop"], 0.03, " drop", color=ACCENT, fontsize=9, ha="left")
    if "tempo_map" in show:
        axes[1].text(0.5, C.FINALE_MAX_RATE + 0.6, "tempo: " + " | ".join(
            f"{g['start']:.0f}-{g['end']:.0f}s {g['bpm']:.0f} BPM" for g in show["tempo_map"]), fontsize=8,
            color=MUTED)

    # 4) launch x position (patterns: sweeps, centre-out, mirrored pairs)
    ax = axes[3]
    ax.scatter([c["t"] for c in aerial], [c["x"] for c in aerial], s=[10 * c["scale"] ** 2 for c in aerial],
               color=SERIES, alpha=0.7, lw=0)
    for c in cues:
        if c["type"] == "fountain":
            ax.plot([c["t"], c["t"] + c["dur"]], [c["x"]] * 2, color=MUTED, lw=1.5, alpha=0.5)
    ax.set_ylim(C.X_RANGE)
    ax.set_ylabel("x (m)", color=MUTED, fontsize=9)
    ax.set_xlabel("song time (s)", color=MUTED, fontsize=9)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=72)
    print(f"wrote {out}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("cues")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    plot(json.loads(Path(args.analysis).read_text()), json.loads(Path(args.cues).read_text(encoding="utf-8")),
         Path(args.out))


if __name__ == "__main__":
    main()
