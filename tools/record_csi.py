"""Labeled CSI recorder for WiFi Space Mapper — Rung 2 (person identification).

Records short *walk* segments tagged with WHO walked and WHICH session, so we can
later train a classifier AND run the honest test (train on one day, test on another).

Why segments, not a continuous dump:
  Identity lives in the temporal gait pattern, so the unit of data is a few seconds
  of someone walking a fixed path — not single frames. Each "take" is one such walk,
  saved as its own labeled file.

What it saves (one .npz per take):
  amp   float32 (T, C)  per-frame subcarrier amplitude |H| (same front end as the
                        detector: SKIP_HEAD_PAIRS dropped, dominant length only)
  t     float64 (T,)    host timestamps per frame (to recover real cadence/fps)
  meta  person / session / take / fps / port — embedded so files are self-describing
Files: data/<session>/<person>_<session>_take<NN>.npz

Usage:
    python tools/record_csi.py <person> [session] [COMx] [baud]
    python tools/record_csi.py gon                 # session defaults to today (YYYYMMDD)
    python tools/record_csi.py alex 20260628 COM9 921600

Protocol (do this identically for both people):
  - Same room, same walking path, same WALK_SEC duration.
  - Press Enter -> walk the path for the countdown -> the take is saved. Repeat.
  - Aim for ~20-30 takes per person per session, and collect on >= 2 different days.
  - Also record a few EMPTY-room takes (person 'empty') as a control / negative class.

Close the ESP-IDF monitor first — only one program can hold the serial port.
"""

import os
import re
import sys
import time
import datetime as dt

import numpy as np
import serial

PERSON = sys.argv[1] if len(sys.argv) > 1 else None
SESSION = sys.argv[2] if len(sys.argv) > 2 else dt.date.today().strftime("%Y%m%d")
PORT = sys.argv[3] if len(sys.argv) > 3 else "COM9"
BAUD = int(sys.argv[4]) if len(sys.argv) > 4 else 921600

SKIP_HEAD_PAIRS = 2       # drop the first (often-invalid) CSI byte-pairs (matches detector)
WARMUP_SETTLE_FPS = 15    # treat the stream as "up" once it exceeds this (skips boot ramp)
WARMUP_MEASURE_SEC = 2.0  # then measure fps + lock the dominant frame length over this window
WALK_SEC = 4.0            # duration of one recorded walk segment
MAX_PER_CYCLE = 2000
RESET_BACKLOG = 65536

OUT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

PATTERN = re.compile(r"CSI_DATA,(\d+),(-?\d+),\[(.*)\]")


def amplitude_from_tokens(tokens):
    """Interleaved (imag, real) signed bytes -> per-subcarrier amplitude |H|."""
    data = np.array(tokens, dtype=float)
    if len(data) < 2 or len(data) % 2:
        return None
    imag, real = data[0::2], data[1::2]
    amp = np.sqrt(real ** 2 + imag ** 2)
    return amp[SKIP_HEAD_PAIRS:] if SKIP_HEAD_PAIRS else amp


def read_frame(ser):
    """Read one parsed amplitude vector from serial, or None if the line wasn't CSI."""
    raw = ser.readline().decode(errors="ignore")
    m = PATTERN.search(raw)
    if not m:
        return None
    try:
        return amplitude_from_tokens(m.group(3).split())
    except ValueError:
        return None


def warmup(ser):
    """Wait for the stream to come up, then measure fps + lock the dominant frame length.

    Same two-step logic as the detector: skip the sparse boot/reconnect ramp (it reads
    fps~1), then average over a multi-second window because USB delivers frames in
    ~16 ms bursts (any short estimate lies)."""
    from collections import deque, Counter
    print("Warming up — waiting for a steady CSI stream...")
    fps_times = deque(maxlen=240)
    len_votes = Counter()
    measure_start = None
    measure_count = 0
    while True:
        amp = read_frame(ser)
        if amp is None:
            continue
        now = time.time()
        fps_times.append(now)
        len_votes[len(amp)] += 1
        if measure_start is None:
            recent_1s = sum(1 for t in fps_times if t >= now - 1.0)
            if recent_1s >= WARMUP_SETTLE_FPS:
                measure_start, measure_count = now, 0
            continue
        measure_count += 1
        if now - measure_start >= WARMUP_MEASURE_SEC:
            fps = measure_count / (now - measure_start)
            target_len = len_votes.most_common(1)[0][0]
            print(f"Stream up: {fps:.0f} fps, locked frame length {target_len} subcarriers.")
            return fps, target_len


def next_take_number(out_dir, person, session):
    """Continue numbering so re-running the recorder appends instead of overwriting."""
    if not os.path.isdir(out_dir):
        return 1
    prefix = f"{person}_{session}_take"
    nums = [int(f[len(prefix):-4]) for f in os.listdir(out_dir)
            if f.startswith(prefix) and f.endswith(".npz") and f[len(prefix):-4].isdigit()]
    return max(nums, default=0) + 1


def record_take(ser, target_len, walk_sec):
    """Drain the backlog, then collect WALK_SEC of frames at the locked length."""
    ser.reset_input_buffer()   # start clean so the take is "from now", not stale frames
    amps, times = [], []
    t0 = time.time()
    while time.time() - t0 < walk_sec:
        if ser.in_waiting > RESET_BACKLOG:
            ser.reset_input_buffer()
        processed = 0
        while ser.in_waiting and processed < MAX_PER_CYCLE:
            processed += 1
            amp = read_frame(ser)
            if amp is None or len(amp) != target_len:
                continue
            amps.append(amp)
            times.append(time.time())
        time.sleep(0.002)
    return np.array(amps, dtype=np.float32), np.array(times, dtype=np.float64)


def main():
    if not PERSON:
        sys.exit("Usage: python tools/record_csi.py <person> [session] [COMx] [baud]\n"
                 "  e.g. python tools/record_csi.py gon\n"
                 "  use person 'empty' for negative/control takes.")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as exc:
        sys.exit(f"Could not open {PORT}: {exc}\n"
                 f"Is the ESP-IDF monitor still open? Close it and retry.")

    out_dir = os.path.join(OUT_ROOT, SESSION)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Recording person={PERSON!r} session={SESSION!r} from {PORT} @ {BAUD}")
    print(f"Saving to {out_dir}")

    fps, target_len = warmup(ser)
    take = next_take_number(out_dir, PERSON, SESSION)

    print("\nReady. Press Enter to record a walk, or type 'q' then Enter to quit.")
    while True:
        cmd = input(f"[take {take:02d}] Enter=record {WALK_SEC:.0f}s walk  /  q=quit > ").strip().lower()
        if cmd == "q":
            break
        print(f"  recording {WALK_SEC:.0f}s — WALK NOW...")
        amp, t = record_take(ser, target_len, WALK_SEC)
        if len(amp) < 0.5 * fps * WALK_SEC:
            print(f"  ⚠ only {len(amp)} frames (expected ~{fps*WALK_SEC:.0f}). "
                  f"Stream may have dropped — take NOT saved, try again.")
            continue
        path = os.path.join(out_dir, f"{PERSON}_{SESSION}_take{take:02d}.npz")
        np.savez(path, amp=amp, t=t,
                 person=PERSON, session=SESSION, take=take, fps=fps, port=PORT)
        secs = t[-1] - t[0] if len(t) > 1 else 0.0
        print(f"  ✔ saved {len(amp)} frames over {secs:.1f}s ({len(amp)/max(secs,1e-6):.0f} fps) -> {os.path.basename(path)}")
        take += 1

    ser.close()
    print(f"\nDone. Takes for {PERSON} this session are in {out_dir}")


if __name__ == "__main__":
    main()
