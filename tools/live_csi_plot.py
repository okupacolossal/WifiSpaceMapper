"""Simple matplotlib CSI viewer + motion detector for WiFi Space Mapper.

This is the lightweight, dependency-minimal viewer (numpy + matplotlib only). For
the full multi-view dashboard (waterfall, radar, I/Q, Doppler) use
`tools/wifi_visualizer.py` instead.

Both share the same engine and detector:
  - tools/csi_stream.py — opens the source and hands out clean amplitude frames.
  - tools/detector.py   — the proven gain-removal -> moving-window-std ->
                          calibrated-threshold -> hysteresis detector.

Two panels:
  TOP    raw per-subcarrier amplitude of the current frame.
  BOTTOM motion level over time + the fixed threshold, with a MOTION/STILL title.

Usage:
    python tools/live_csi_plot.py                 # demo mode (synthetic, no hardware)
    python tools/live_csi_plot.py --demo
    python tools/live_csi_plot.py COM9 921600     # live ESP32
    python tools/live_csi_plot.py COM5 115200

Close the ESP-IDF monitor first — only one program can hold the port. Opening the
port resets the ESP32, so expect a few seconds of boot + reconnect before frames.
"""

import sys
import time
from collections import deque

import numpy as np
import matplotlib.pyplot as plt

from csi_stream import CSIStream, make_source
from detector import MotionDetector

HISTORY = 300            # motion points shown on the time plot


def main():
    args = sys.argv[1:]
    spec = args[0] if args else "--demo"
    baud = int(args[1]) if len(args) > 1 else 921600

    try:
        stream = CSIStream(make_source(spec, baud)).start()
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"Reading CSI from {stream.label}.")

    det = MotionDetector()
    motion_hist = deque(maxlen=HISTORY)

    plt.ion()
    fig, (ax_raw, ax_mot) = plt.subplots(2, 1, figsize=(8, 6))
    (raw_line,) = ax_raw.plot([], [], lw=1)
    ax_raw.set_title("CSI subcarrier amplitude (current frame)")
    ax_raw.set_xlabel("subcarrier index")
    ax_raw.set_ylabel("amplitude |H|")

    (mot_line,) = ax_mot.plot([], [], lw=1.5)
    thr_line = ax_mot.axhline(0.0, color="r", ls="--", lw=1, label="threshold")
    ax_mot.set_title("Motion — starting...")
    ax_mot.set_xlabel("recent frames")
    ax_mot.set_ylabel("motion level")
    ax_mot.set_xlim(0, HISTORY)
    ax_mot.legend(loc="upper right")
    fig.tight_layout()

    last_amp = None
    while plt.fignum_exists(fig.number):
        frames = stream.drain()
        for f in frames:
            st = det.update(f.amp, f.t)
            last_amp = f.amp
            if st.motion_level is not None:
                motion_hist.append(st.motion_level)

        if not frames:
            plt.pause(0.01)
            continue

        st = det._snapshot()
        if last_amp is not None:
            raw_line.set_data(np.arange(len(last_amp)), last_amp)
            ax_raw.relim(); ax_raw.autoscale_view()
            ax_raw.set_title(f"CSI subcarrier amplitude   |   {stream.fps:5.1f} frames/sec   "
                             f"({len(last_amp)} subcarriers)")

        if motion_hist:
            ys = np.array(motion_hist)
            mot_line.set_data(np.arange(len(ys)), ys)
            top = max(ys.max(), st.threshold or 0.0) * 1.2 + 1e-4
            ax_mot.set_ylim(0, top)

        if st.phase == "warmup":
            ax_mot.set_title("Motion — WARMUP (measuring rate, locking frame type)...")
        elif st.phase == "calibrate":
            ax_mot.set_title(f"Motion — CALIBRATING, STAY STILL ({st.calib_progress*100:.0f}%)")
            mot_line.set_color("C0")
        else:
            thr_line.set_ydata([st.threshold, st.threshold])
            mot_line.set_color("red" if st.in_motion else "green")
            cur = motion_hist[-1] if motion_hist else 0.0
            ax_mot.set_title(
                f"{'>>> MOTION DETECTED <<<' if st.in_motion else 'still'}   "
                f"(level={cur:.4f}, thr={st.threshold:.4f})")

        plt.pause(0.001)

    stream.stop()
    print(f"Stopped. Parsed {stream.total} CSI frames.")


if __name__ == "__main__":
    main()
