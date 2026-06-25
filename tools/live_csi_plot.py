"""Live CSI viewer + motion detector for the WiFi Space Mapper firmware.

Reads the firmware's serial output ("CSI_DATA,<len>,<rssi>,[...]" lines) and
shows two panels:

  TOP    raw per-subcarrier amplitude of the current frame (the jittery shape).
  BOTTOM a single "motion" number over time = how much the channel *shape*
         changes frame-to-frame, with an auto-adapting threshold anchored to the
         quiet floor. Still room -> low flat line; motion -> spikes above it.

Key ideas:
- Motion lives in how consecutive frames differ, not in any single frame.
- Normalize out the radio's gain (AGC) first so only genuine shape changes count.
- The ESP32 emits different CSI lengths per packet type, so we only diff two
  frames when they share a length (consecutive same-type frames do).
- We DRAIN the serial backlog every cycle and redraw once, so the plot always
  shows fresh data instead of lagging seconds behind the stream.

Usage:
    python tools/live_csi_plot.py            # COM3 @ 115200
    python tools/live_csi_plot.py COM5 921600

Close the ESP-IDF monitor first — only one program can hold the port. Opening
the port resets the ESP32, so expect a few seconds of boot + reconnect first.
"""

import re
import sys
from collections import deque

import numpy as np
import serial
import matplotlib.pyplot as plt

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM3"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

HISTORY = 300         # motion points shown on the time plot
BASE_WINDOW = 400     # recent motion samples for the adaptive (floor) baseline
SKIP_HEAD_PAIRS = 2   # drop the first (often-invalid) CSI byte-pairs
THRESH_K = 3.0        # threshold = floor + K * (median - floor)
SMOOTH = 3            # moving-average length to de-noise the motion line
MAX_PER_CYCLE = 500   # max serial lines to process before redrawing
RESET_BACKLOG = 65536 # if this many bytes pile up, drop stale data to stay live

PATTERN = re.compile(r"CSI_DATA,(\d+),(-?\d+),\[(.*)\]")


def amplitude_from_tokens(tokens):
    """Interleaved (imag, real) signed bytes -> per-subcarrier amplitude."""
    data = np.array(tokens, dtype=float)
    if len(data) < 2 or len(data) % 2:
        return None
    imag, real = data[0::2], data[1::2]
    amp = np.sqrt(real ** 2 + imag ** 2)
    return amp[SKIP_HEAD_PAIRS:] if SKIP_HEAD_PAIRS else amp


def main():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as exc:
        sys.exit(f"Could not open {PORT}: {exc}\n"
                 f"Is the ESP-IDF monitor still open? Close it and retry.")

    print(f"Reading CSI from {PORT} @ {BAUD}. Threshold adapts automatically.")

    motion_hist = deque(maxlen=HISTORY)
    smooth_buf = deque(maxlen=SMOOTH)
    recent = deque(maxlen=BASE_WINDOW)
    threshold = None
    prev_norm = None
    last_amp = None

    plt.ion()
    fig, (ax_raw, ax_mot) = plt.subplots(2, 1, figsize=(8, 6))

    (raw_line,) = ax_raw.plot([], [], lw=1)
    ax_raw.set_title("CSI subcarrier amplitude (current frame)")
    ax_raw.set_xlabel("subcarrier index")
    ax_raw.set_ylabel("amplitude")

    (mot_line,) = ax_mot.plot([], [], lw=1.5)
    thr_line = ax_mot.axhline(0.0, color="r", ls="--", lw=1, label="threshold")
    ax_mot.set_title("Motion — warming up...")
    ax_mot.set_xlabel("recent frames")
    ax_mot.set_ylabel("motion level")
    ax_mot.set_xlim(0, HISTORY)
    ax_mot.legend(loc="upper right")
    fig.tight_layout()

    raw_lines = seen = 0
    while plt.fignum_exists(fig.number):
        # If a big backlog has built up, drop it so we display live, not lagged.
        if ser.in_waiting > RESET_BACKLOG:
            ser.reset_input_buffer()

        # Drain everything currently available; compute motion for each frame.
        got = False
        processed = 0
        while ser.in_waiting and processed < MAX_PER_CYCLE:
            processed += 1
            raw = ser.readline().decode(errors="ignore")
            if raw:
                raw_lines += 1
            m = PATTERN.search(raw)
            if not m:
                if raw_lines and raw_lines % 50 == 0 and seen == 0:
                    print(f"read {raw_lines} lines, no CSI_DATA yet. "
                          f"Last line: {raw.strip()[:80]!r}")
                continue
            try:
                amp = amplitude_from_tokens(m.group(3).split())
            except ValueError:
                continue
            if amp is None:
                continue

            seen += 1
            last_amp = amp
            got = True

            norm = amp / (amp.mean() + 1e-6)
            if prev_norm is not None and len(prev_norm) == len(norm):
                motion = float(np.mean(np.abs(norm - prev_norm)))
                smooth_buf.append(motion)
                motion_s = float(np.mean(smooth_buf))
                motion_hist.append(motion_s)
                recent.append(motion_s)
            prev_norm = norm

        if not got:
            plt.pause(0.005)
            continue

        # Adaptive threshold anchored to the quiet FLOOR (20th percentile), so
        # continuous motion stays above it instead of dragging it up.
        if len(recent) >= 20:
            r = np.array(recent)
            floor = float(np.percentile(r, 20))
            spread = float(np.median(r) - floor)
            threshold = floor + THRESH_K * max(spread, 1e-4)

        # --- redraw once per cycle ---
        raw_line.set_data(np.arange(len(last_amp)), last_amp)
        ax_raw.relim()
        ax_raw.autoscale_view()

        if motion_hist:
            ys = np.array(motion_hist)
            mot_line.set_data(np.arange(len(ys)), ys)
            ax_mot.set_ylim(0, max(ys.max(), threshold or 0.0) * 1.2 + 1e-3)
            cur = float(ys[-1])
            if threshold is None:
                ax_mot.set_title("Motion — warming up...")
                mot_line.set_color("C0")
            else:
                thr_line.set_ydata([threshold, threshold])
                detected = cur > threshold
                mot_line.set_color("red" if detected else "green")
                ax_mot.set_title(
                    f"Motion: {'MOTION DETECTED' if detected else 'still'}   "
                    f"(level={cur:.3f}, thr={threshold:.3f})")

        plt.pause(0.001)

    ser.close()
    print(f"Stopped. Parsed {seen} CSI frames from {raw_lines} serial lines.")


if __name__ == "__main__":
    main()
