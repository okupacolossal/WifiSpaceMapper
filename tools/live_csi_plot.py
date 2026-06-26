"""Initial CSI visualizer for the WiFi Space Mapper firmware.

Reads the firmware's serial output ("CSI_DATA,<len>,<rssi>,[...]" lines) and
live-plots the per-subcarrier amplitude of the current frame. The title shows
the measured frames/sec so you can see the Stage-3 self-ping + 921600 baud work
(should read ~20-25 fps, vs ~7-10 fps before).

This is deliberately minimal — just "see the channel react." Wave a hand or walk
through the link and the curve should jump. The real motion detector comes next.

Usage:
    python tools/live_csi_plot.py             # COM9 @ 921600 (current firmware)
    python tools/live_csi_plot.py COM5 115200

Close the ESP-IDF monitor first — only one program can hold the port. Opening the
port resets the ESP32, so expect a few seconds of boot + reconnect before frames.
"""

import re
import sys
import time
from collections import deque

import numpy as np
import serial
import matplotlib.pyplot as plt

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM9"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 921600

SKIP_HEAD_PAIRS = 2      # drop the first (often-invalid) CSI byte-pairs
MAX_PER_CYCLE = 600      # max serial lines to process before redrawing
RESET_BACKLOG = 65536    # if this many bytes pile up, drop stale data to stay live

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

    print(f"Reading CSI from {PORT} @ {BAUD}. Close this window to stop.")

    plt.ion()
    fig, ax = plt.subplots(figsize=(8, 5))
    (amp_line,) = ax.plot([], [], lw=1)
    ax.set_title("CSI subcarrier amplitude — warming up...")
    ax.set_xlabel("subcarrier index")
    ax.set_ylabel("amplitude |H|")
    fig.tight_layout()

    last_amp = None
    fps_times = deque(maxlen=200)   # timestamps of recent frames, for the fps readout
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
            fps_times.append(time.time())
            got = True

        if not got:
            plt.pause(0.005)
            continue

        # live fps from the spread of recent frame timestamps
        fps = 0.0
        if len(fps_times) >= 2:
            span = fps_times[-1] - fps_times[0]
            if span > 0:
                fps = (len(fps_times) - 1) / span

        amp_line.set_data(np.arange(len(last_amp)), last_amp)
        ax.relim()
        ax.autoscale_view()
        ax.set_title(f"CSI subcarrier amplitude   |   {fps:4.1f} frames/sec   "
                     f"({len(last_amp)} subcarriers)")
        plt.pause(0.001)

    ser.close()
    print(f"Stopped. Parsed {seen} CSI frames from {raw_lines} serial lines.")


if __name__ == "__main__":
    main()
