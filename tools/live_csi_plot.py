"""Live CSI viewer + straightforward motion detector for WiFi Space Mapper.

Two panels:
  TOP    raw per-subcarrier amplitude of the current frame (the jittery shape).
  BOTTOM a single motion level over time + a fixed threshold, with a big
         MOTION / STILL readout in the title.

How the detector works (and why it's not the naive version that failed):
  1. Gain removal  — divide each frame's amplitude by its own mean. The ESP32's
     AGC scales the whole vector per packet; dividing it out leaves only the
     channel *shape*, so gain wobble doesn't look like motion.
  2. Window, not frame-diff — keep the last WINDOW normalized shapes and measure
     how much each subcarrier wiggles over that window (std over time). Still
     room -> shapes barely change -> low. Motion -> shapes churn -> high.
  3. Calibrated threshold — learn the "still" level for a while at startup and
     fix the threshold from it, so continuous motion can't drag it upward.
  4. Hysteresis — separate enter/exit levels so the readout doesn't flicker.

The ESP32 emits a couple of CSI frame lengths (e.g. legacy beacons vs HT ping
replies). We lock onto the most common one so the window stays consistent.
Phases are driven by FRAME COUNT (not time), so they wait for the steady stream
no matter how long the boot + Wi-Fi reconnect takes.

Phases at startup (watch the title):
  WARMUP    lock onto the dominant CSI frame length.
  CALIBRATE STAY STILL — learns the quiet baseline.
  DETECT    live MOTION / STILL.

Usage:
    python tools/live_csi_plot.py             # COM9 @ 921600 (current firmware)
    python tools/live_csi_plot.py COM5 115200

Close the ESP-IDF monitor first — only one program can hold the port. Opening the
port resets the ESP32, so expect a few seconds of boot + reconnect before frames.
"""

import re
import sys
import time
from collections import deque, Counter

import numpy as np
import serial
import matplotlib.pyplot as plt

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM9"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 921600

SKIP_HEAD_PAIRS = 2     # drop the first (often-invalid) CSI byte-pairs
WINDOW = 48             # frames in the moving-std window (~2 s at 23 fps)
WARMUP_FRAMES = 200      # frames to sample before locking the dominant length
CALIB_FRAMES = 300      # still motion-samples to learn the baseline from
THRESH_MULT = 1.4       # threshold = P95(still motion) * this
HYST_LOW = 0.6          # exit-motion level = HYST_LOW * threshold
HISTORY = 300           # motion points shown on the time plot
MAX_PER_CYCLE = 600
RESET_BACKLOG = 65536

PATTERN = re.compile(r"CSI_DATA,(\d+),(-?\d+),\[(.*)\]")


def amplitude_from_tokens(tokens):
    """Interleaved (imag, real) signed bytes -> per-subcarrier amplitude |H|."""
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

    print(f"Reading CSI from {PORT} @ {BAUD}.")

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

    # detector state
    phase = "warmup"
    len_votes = Counter()          # frame-length histogram during warmup
    target_len = None              # locked dominant length
    shapes = deque(maxlen=WINDOW)  # recent normalized shapes (all == target_len)
    baseline = []                  # still motion levels gathered during calibrate
    threshold = None
    motion_state = False           # False = still, True = motion (hysteresis)
    motion_hist = deque(maxlen=HISTORY)
    fps_times = deque(maxlen=120)   # recent frame timestamps -> live fps readout

    last_amp = None
    seen = raw_lines = 0

    while plt.fignum_exists(fig.number):
        if ser.in_waiting > RESET_BACKLOG:
            ser.reset_input_buffer()

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
                          f"Last: {raw.strip()[:80]!r}")
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
            fps_times.append(time.time())

            # --- WARMUP: pick the dominant frame length once enough frames seen ---
            if phase == "warmup":
                len_votes[len(amp)] += 1
                if sum(len_votes.values()) >= WARMUP_FRAMES:
                    target_len = len_votes.most_common(1)[0][0]
                    phase = "calibrate"
                    print(f"locked frame length = {target_len} subcarriers "
                          f"(histogram {dict(len_votes)}); calibrating — STAY STILL")
                continue

            if len(amp) != target_len:
                continue   # ignore off-type frames so the window stays consistent

            # gain removal: keep only the channel shape, not the AGC level
            shape = amp / (amp.mean() + 1e-6)
            shapes.append(shape)
            if len(shapes) < WINDOW:
                continue

            # motion level = how much each subcarrier wiggles over the window
            arr = np.array(shapes)               # (WINDOW, target_len)
            motion = float(arr.std(axis=0).mean())
            motion_hist.append(motion)

            # --- CALIBRATE: collect the still baseline, then fix the threshold ---
            if phase == "calibrate":
                baseline.append(motion)
                if len(baseline) >= CALIB_FRAMES:
                    threshold = max(float(np.percentile(baseline, 95) * THRESH_MULT), 1e-4)
                    phase = "detect"
                    print(f"baseline median={np.median(baseline):.4f} "
                          f"-> threshold={threshold:.4f}; detecting")
                continue

            # --- DETECT: hysteresis state machine ---
            if motion_state and motion < threshold * HYST_LOW:
                motion_state = False
            elif (not motion_state) and motion > threshold:
                motion_state = True

        if not got:
            plt.pause(0.005)
            continue

        # ---- redraw ----
        fps = 0.0
        if len(fps_times) >= 2 and fps_times[-1] > fps_times[0]:
            fps = (len(fps_times) - 1) / (fps_times[-1] - fps_times[0])

        raw_line.set_data(np.arange(len(last_amp)), last_amp)
        ax_raw.relim(); ax_raw.autoscale_view()
        ax_raw.set_title(f"CSI subcarrier amplitude   |   {fps:5.1f} frames/sec   "
                         f"({len(last_amp)} subcarriers)")

        if motion_hist:
            ys = np.array(motion_hist)
            mot_line.set_data(np.arange(len(ys)), ys)
            top = max(ys.max(), threshold or 0.0) * 1.2 + 1e-4
            ax_mot.set_ylim(0, top)

        if phase == "warmup":
            n = sum(len_votes.values())
            ax_mot.set_title(f"Motion — WARMUP (locking frame type {n}/{WARMUP_FRAMES})...")
        elif phase == "calibrate":
            ax_mot.set_title(f"Motion — CALIBRATING, STAY STILL ({len(baseline)}/{CALIB_FRAMES})")
            mot_line.set_color("C0")
        else:
            thr_line.set_ydata([threshold, threshold])
            mot_line.set_color("red" if motion_state else "green")
            cur = motion_hist[-1] if motion_hist else 0.0
            ax_mot.set_title(
                f"{'>>> MOTION DETECTED <<<' if motion_state else 'still'}   "
                f"(level={cur:.4f}, thr={threshold:.4f})")

        plt.pause(0.001)

    ser.close()
    print(f"Stopped. Parsed {seen} CSI frames from {raw_lines} serial lines.")


if __name__ == "__main__":
    main()
