"""Generate dark-themed PREVIEW images of the visualizer's views from synthetic CSI.

These PNGs (written to docs/media/) let the README show the views without a board
attached. They are rendered with matplotlib from the SAME synthetic source and the
SAME detector the app uses — so they're representative — but they are NOT screenshots
of the PyQtGraph GUI. Capture real GUI screenshots/GIFs on a machine with a display
and a board to replace them.

Run:  python tools/make_previews.py
"""

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

from csi_stream import SyntheticSource
from detector import MotionDetector

BG, PANEL, FG, MUTED = "#0e1116", "#161b22", "#c9d1d9", "#8b949e"
ACCENT, CYAN, GREEN, RED, AMBER = "#6e4aff", "#39d0d8", "#3fb950", "#f85149", "#d29922"
CMAP = "inferno"

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "media")


def style(ax):
    ax.set_facecolor(PANEL)
    for s in ax.spines.values():
        s.set_color("#30363d")
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    ax.title.set_color(FG)
    ax.grid(True, color="#21262d", alpha=0.5, lw=0.6)


def capture(seconds=32.0, fps=90.0):
    """Run the synthetic source through the detector; return the recorded arrays."""
    dt = 1.0 / fps
    src = SyntheticSource(fps=fps)
    det = MotionDetector()
    amps, levels, motion_flags, frames = [], [], [], []
    last_motion_frame = None
    for i in range(int(seconds * fps)):
        e = i * dt
        amp, _, imag, real = src.sample(e)
        st = det.update(amp, t=src._t0 + e)
        amps.append(amp)
        if st.phase == "detect":
            levels.append(st.motion_level)
            motion_flags.append(st.in_motion)
            if st.in_motion:
                last_motion_frame = (amp, real, imag)
        elif st.motion_level is not None:
            levels.append(st.motion_level)
            motion_flags.append(False)
    return (np.array(amps), np.array(levels), np.array(motion_flags),
            det.threshold, fps, last_motion_frame, amps[5])


def fig(w, h):
    f = plt.figure(figsize=(w, h), dpi=110)
    f.patch.set_facecolor(BG)
    return f


def save(f, name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    f.savefig(path, facecolor=BG, bbox_inches="tight")
    plt.close(f)
    print(f"  wrote docs/media/{name}")


def main():
    amps, levels, flags, thr, fps, motion_frame, still_frame = capture()
    motion_amp, motion_real, motion_imag = motion_frame
    wf = amps[-256:].T                       # (subcarrier, time)
    hi = np.percentile(wf, 99)
    x = np.arange(len(levels))

    # --- spectrogram / waterfall ---
    f = fig(9, 4.2); ax = f.add_subplot(111); style(ax)
    ax.imshow(wf, aspect="auto", origin="lower", cmap=CMAP, vmin=0, vmax=hi,
              extent=[0, wf.shape[1], 0, wf.shape[0]])
    ax.set_title("Spectrogram — subcarrier × time (colour = amplitude)")
    ax.set_xlabel("time (frames) →"); ax.set_ylabel("subcarrier")
    save(f, "spectrogram.png")

    # --- motion timeline ---
    f = fig(9, 4.2); ax = f.add_subplot(111); style(ax)
    ax.plot(x, levels, color=CYAN, lw=1.4)
    if thr:
        ax.axhline(thr, color=RED, ls="--", lw=1.2, label="threshold")
    ax.fill_between(x, 0, levels, where=flags, color=RED, alpha=0.18, label="MOTION")
    ax.set_title("Motion level vs threshold (shaded = detected motion)")
    ax.set_xlabel("frame"); ax.set_ylabel("motion level")
    ax.legend(facecolor=PANEL, edgecolor="#30363d", labelcolor=FG, fontsize=8)
    save(f, "motion.png")

    # --- raw CSI: current frame + still contrast ---
    f = fig(9, 4.2); ax = f.add_subplot(111); style(ax)
    ax.plot(still_frame, color=MUTED, lw=1.0, alpha=0.7, label="still frame")
    ax.plot(motion_amp, color=CYAN, lw=2.0, label="frame during motion")
    ax.set_title("Raw per-subcarrier amplitude |H|")
    ax.set_xlabel("subcarrier index"); ax.set_ylabel("|H|")
    ax.legend(facecolor=PANEL, edgecolor="#30363d", labelcolor=FG, fontsize=8)
    save(f, "raw_csi.png")

    # --- radar / radial ---
    f = fig(5.2, 5.2); ax = f.add_subplot(111, projection="polar")
    ax.set_facecolor(PANEL); f.patch.set_facecolor(BG)
    r = motion_amp / (motion_amp.max() + 1e-6)
    th = np.linspace(0, 2 * np.pi, len(r), endpoint=False)
    th = np.append(th, th[0]); r = np.append(r, r[0])
    ax.plot(th, r, color=CYAN, lw=2)
    ax.fill(th, r, color=ACCENT, alpha=0.25)
    ax.set_title("Radial CSI — subcarriers around the circle", color=FG, pad=18)
    ax.tick_params(colors=MUTED, labelsize=7)
    ax.set_yticklabels([])
    save(f, "radar.png")

    # --- doppler / motion spectrum ---
    f = fig(9, 4.2); ax = f.add_subplot(111); style(ax)
    seg = levels[-256:] if len(levels) >= 64 else levels
    seg = (seg - seg.mean()) * np.hanning(len(seg))
    mag = np.abs(np.fft.rfft(seg)); freqs = np.fft.rfftfreq(len(seg), d=1.0 / fps)
    ax.plot(freqs, mag, color=CYAN, lw=1.6)
    ax.fill_between(freqs, 0, mag, color=CYAN, alpha=0.15)
    ax.set_title("Doppler — frequency content of the motion signal")
    ax.set_xlabel("frequency (Hz)"); ax.set_ylabel("power"); ax.set_xlim(0, 15)
    save(f, "doppler.png")

    # --- dashboard composite (hero) ---
    f = fig(11, 6); gs = GridSpec(2, 2, figure=f, height_ratios=[2, 1.4], hspace=0.35, wspace=0.22)
    a0 = f.add_subplot(gs[0, 0]); style(a0)
    a0.plot(motion_amp, color=ACCENT, lw=2); a0.set_title("Raw CSI amplitude")
    a0.set_xlabel("subcarrier"); a0.set_ylabel("|H|")
    a1 = f.add_subplot(gs[0, 1]); style(a1)
    a1.imshow(wf, aspect="auto", origin="lower", cmap=CMAP, vmin=0, vmax=hi,
              extent=[0, wf.shape[1], 0, wf.shape[0]])
    a1.set_title("Spectrogram"); a1.set_xlabel("time →"); a1.set_ylabel("subcarrier")
    a2 = f.add_subplot(gs[1, :]); style(a2)
    a2.plot(x, levels, color=GREEN, lw=1.4)
    if thr:
        a2.axhline(thr, color=RED, ls="--", lw=1.2)
    a2.fill_between(x, 0, levels, where=flags, color=RED, alpha=0.18)
    a2.set_title("Motion level vs threshold"); a2.set_xlabel("frame"); a2.set_ylabel("level")
    f.suptitle("WiFi Space Mapper — CSI Visualizer  (synthetic demo data)",
               color=FG, fontsize=13, y=0.98)
    save(f, "dashboard.png")


if __name__ == "__main__":
    main()
