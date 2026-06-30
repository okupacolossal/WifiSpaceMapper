"""Shared CSI streaming engine for WiFi Space Mapper.

One place to turn the ESP32's serial output into clean, per-frame subcarrier
amplitude vectors — reused by the live detector and the visualizer so there is a
single source of truth for "what a CSI frame is."

The firmware prints one line per received Wi-Fi frame:

    CSI_DATA,<len>,<rssi>,[I,Q,I,Q, ...]

where the bracketed list is interleaved signed (imag, real) byte pairs, one pair
per OFDM subcarrier. Amplitude is |H| = sqrt(I^2 + Q^2); we keep amplitude and
drop phase (too noisy on a single radio without clock sync).

A `CSISource` yields `Frame` objects and hides WHERE frames come from:
  - `SerialSource`    — a live ESP32 on a COM port (the real thing).
  - `ReplaySource`    — a recorded .npz from record_csi.py, replayed at real cadence.
  - `SyntheticSource` — believable fake CSI so the whole app runs with no hardware
                        (this is what `--demo` uses).

`CSIStream` wraps any source in a background thread so the UI never blocks on I/O,
tracks a live frames/sec figure, and hands out frames via `drain()` (every frame
since the last call — so the detector's moving window stays gap-free) or
`latest()` (just the newest, for cheap repaints).
"""

import os
import re
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, List

import numpy as np

# Matches the firmware's line format; group 1 = #values, 2 = rssi, 3 = the I/Q list.
PATTERN = re.compile(r"CSI_DATA,(\d+),(-?\d+),\[(.*)\]")

# Drop the first CSI byte-pairs — on real hardware these lead pairs are often
# invalid. Kept identical to live_csi_plot.py / record_csi.py so every tool agrees
# on the subcarrier vector.
SKIP_HEAD_PAIRS = 2


def decode_tokens(tokens, skip_head_pairs=SKIP_HEAD_PAIRS):
    """Interleaved (imag, real) signed bytes -> (amplitude, imag, real) arrays.

    amplitude is |H| = sqrt(I^2 + Q^2). imag/real are kept too so views that want
    the complex channel (e.g. the I/Q constellation) can have it; everything else
    just uses amplitude. Returns None if the token list is malformed.
    """
    data = np.array(tokens, dtype=float)
    if len(data) < 2 or len(data) % 2:
        return None
    imag, real = data[0::2], data[1::2]
    amp = np.sqrt(real ** 2 + imag ** 2)
    if skip_head_pairs:
        return amp[skip_head_pairs:], imag[skip_head_pairs:], real[skip_head_pairs:]
    return amp, imag, real


def amplitude_from_tokens(tokens, skip_head_pairs=SKIP_HEAD_PAIRS):
    """Interleaved (imag, real) signed bytes -> per-subcarrier amplitude |H|."""
    dec = decode_tokens(tokens, skip_head_pairs)
    return None if dec is None else dec[0]


def parse_csi_line(line, skip_head_pairs=SKIP_HEAD_PAIRS):
    """Parse one serial line into (rssi, amp, imag, real) or None if it isn't CSI."""
    m = PATTERN.search(line)
    if not m:
        return None
    try:
        dec = decode_tokens(m.group(3).split(), skip_head_pairs)
    except ValueError:
        return None
    if dec is None:
        return None
    amp, imag, real = dec
    return int(m.group(2)), amp, imag, real


@dataclass
class Frame:
    """One CSI measurement handed to the rest of the app."""
    amp: np.ndarray                  # per-subcarrier amplitude |H|
    rssi: Optional[int]              # frame RSSI in dBm (None if unknown)
    t: float                         # host arrival timestamp (time.time())
    imag: Optional[np.ndarray] = None  # raw I (None for replay sources)
    real: Optional[np.ndarray] = None  # raw Q (None for replay sources)


# --------------------------------------------------------------------------- #
# Sources — same tiny interface: read() returns a Frame, or None if nothing is
# ready yet. close() releases any held resource (serial port, file).
# --------------------------------------------------------------------------- #
class SerialSource:
    """Live frames from an ESP32 over USB serial."""

    name = "serial"

    def __init__(self, port="COM9", baud=921600, reset_backlog=65536):
        import serial  # imported lazily so --demo works without pyserial
        try:
            self._ser = serial.Serial(port, baud, timeout=1)
        except serial.SerialException as exc:
            raise RuntimeError(
                f"Could not open {port} @ {baud}: {exc}\n"
                f"Is the ESP-IDF monitor still open? Close it and retry."
            ) from exc
        self.label = f"{port} @ {baud}"
        self._reset_backlog = reset_backlog

    def read(self):
        ser = self._ser
        # If we've fallen far behind (UI busy), drop stale bytes so we stay live.
        if ser.in_waiting > self._reset_backlog:
            ser.reset_input_buffer()
        if not ser.in_waiting:
            return None
        raw = ser.readline().decode(errors="ignore")  # boot log has non-UTF8 bytes
        parsed = parse_csi_line(raw)
        if parsed is None:
            return None
        rssi, amp, imag, real = parsed
        return Frame(amp=amp, rssi=rssi, t=time.time(), imag=imag, real=real)

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


class ReplaySource:
    """Replays a recorded .npz (from record_csi.py) at its original cadence.

    Lets the visualizer run on real captured data with no board attached. Loops
    forever so a short take still makes a continuous demo.
    """

    name = "replay"

    def __init__(self, npz_path, loop=True):
        data = np.load(npz_path, allow_pickle=True)
        self._amp = np.asarray(data["amp"], dtype=float)
        t = np.asarray(data["t"], dtype=float) if "t" in data else None
        if t is not None and len(t) == len(self._amp):
            self._dt = np.diff(t, prepend=t[0])          # real inter-frame gaps
            self._dt[self._dt <= 0] = 1.0 / 90.0
        else:
            self._dt = np.full(len(self._amp), 1.0 / 90.0)
        self._loop = loop
        self._i = 0
        self._next_t = time.time()
        self.label = os.path.basename(npz_path)

    def read(self):
        if self._i >= len(self._amp):
            if not self._loop:
                return None
            self._i = 0
            self._next_t = time.time()
        now = time.time()
        if now < self._next_t:
            return None                                   # not time for the next frame yet
        amp = self._amp[self._i]
        self._next_t = now + float(self._dt[self._i])
        self._i += 1
        return Frame(amp=amp, rssi=None, t=now)

    def close(self):
        pass


class SyntheticSource:
    """Generates believable CSI so the app is fully usable with no hardware.

    The model mirrors the two things the real signal does that the detector must
    cope with:
      - a stable per-subcarrier channel "shape" (guard nulls at the band edges,
        a DC dip in the middle, bumpy multipath in between), and
      - a per-packet AGC gain that scales the WHOLE vector (the thing gain-removal
        cancels) — so the demo exercises the same failure mode as a real ESP32.
    Motion is injected on a schedule: the shape's per-subcarrier values churn,
    which is exactly what a moving body does to the multipath. The first seconds
    are held still so the detector can calibrate a quiet baseline.
    """

    name = "synthetic"
    N = 64
    GUARD = [0, 1, 2, 3, 60, 61, 62, 63]
    DC = 32

    def __init__(self, fps=90.0, seed=0):
        self.label = "demo (synthetic)"
        self._fps = fps
        self._rng = np.random.default_rng(seed)
        # A fixed, believable static channel magnitude across the 64 subcarriers.
        base = 18 + 8 * np.sin(np.linspace(0, 3 * np.pi, self.N))
        base = base + self._rng.normal(0, 1.5, self.N)
        base = np.clip(base, 3.0, None)
        base[self.GUARD] = self._rng.uniform(0.2, 1.0, len(self.GUARD))  # guard nulls
        base[self.DC] *= 0.3                                             # DC dip
        self._base = base
        self._phase0 = self._rng.uniform(0, 2 * np.pi, self.N)  # per-subcarrier phase
        self._perturb = np.zeros(self.N)   # AR(1) per-subcarrier motion state
        self._t0 = time.time()
        self._next_t = self._t0

    def _is_motion(self, elapsed):
        # Hold still for the first 12 s (covers the detector's ~8 s calibration),
        # then alternate 4 s motion / 4 s still forever.
        if elapsed < 12.0:
            return False
        return ((elapsed - 12.0) % 8.0) < 4.0

    def sample(self, elapsed):
        """Generate one frame's (amp, rssi, imag, real) for a given elapsed-seconds
        value, advancing the internal motion state. Used by read() (real-time paced)
        and by tests / offline preview rendering (stepped on a virtual clock)."""
        if self._is_motion(elapsed):
            # drive the perturbation -> the normalized shape churns -> "motion"
            self._perturb = 0.85 * self._perturb + self._rng.normal(0, 0.22, self.N)
            phase_jitter = self._rng.normal(0, 0.3, self.N)
        else:
            self._perturb = 0.6 * self._perturb + self._rng.normal(0, 0.01, self.N)
            phase_jitter = self._rng.normal(0, 0.02, self.N)

        shape = np.clip(self._base * (1.0 + self._perturb), 0.1, None)
        agc = 1.0 + 0.15 * np.sin(2 * np.pi * 0.3 * elapsed) + self._rng.normal(0, 0.03)
        amp = np.clip(shape * agc + self._rng.normal(0, 0.3, self.N), 0.0, None)
        # complex channel for the I/Q view: slow rotation + per-subcarrier offset.
        phase = self._phase0 + 0.6 * elapsed + phase_jitter
        imag, real = amp * np.sin(phase), amp * np.cos(phase)
        rssi = int(round(-60 + self._rng.normal(0, 2)))
        s = SKIP_HEAD_PAIRS
        return amp[s:], rssi, imag[s:], real[s:]

    def read(self):
        now = time.time()
        if now < self._next_t:
            return None
        self._next_t = now + 1.0 / self._fps
        amp, rssi, imag, real = self.sample(now - self._t0)
        return Frame(amp=amp, rssi=rssi, t=now, imag=imag, real=real)

    def close(self):
        pass


def make_source(spec, baud=921600):
    """Build a source from a CLI-style spec.

    spec:
      "--demo" / "demo" / None    -> SyntheticSource
      a path ending in ".npz"     -> ReplaySource
      anything else (e.g. "COM9") -> SerialSource(spec, baud)
    """
    if spec is None or spec in ("--demo", "demo"):
        return SyntheticSource()
    if isinstance(spec, str) and spec.lower().endswith(".npz"):
        return ReplaySource(spec)
    return SerialSource(spec, baud)


# --------------------------------------------------------------------------- #
# Threaded reader
# --------------------------------------------------------------------------- #
class CSIStream:
    """Runs a source in a background thread; never blocks the UI.

    The reader thread pulls frames as fast as the source provides them and buffers
    them. The UI thread calls `drain()` on a timer to get every frame since last
    time (gap-free, so the detector's moving window is correct) or `latest()` for
    a cheap "newest frame" repaint. `fps` is measured live from arrival times.
    """

    def __init__(self, source, maxlen=4096):
        self._source = source
        self._buf = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._latest = None
        self._fps_times = deque(maxlen=240)
        self._running = False
        self._thread = None
        self.total = 0
        self.label = getattr(source, "label", getattr(source, "name", "source"))

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while self._running:
            try:
                frame = self._source.read()
            except Exception:
                # A transient parse/IO hiccup shouldn't kill the stream thread.
                frame = None
            if frame is None:
                time.sleep(0.001)
                continue
            with self._lock:
                self._buf.append(frame)
                self._latest = frame
                self._fps_times.append(frame.t)
                self.total += 1

    def drain(self) -> List[Frame]:
        """Return (and clear) every frame buffered since the last call."""
        with self._lock:
            frames = list(self._buf)
            self._buf.clear()
        return frames

    def latest(self) -> Optional[Frame]:
        with self._lock:
            return self._latest

    @property
    def fps(self) -> float:
        with self._lock:
            ts = self._fps_times
            if len(ts) >= 2 and ts[-1] > ts[0]:
                return (len(ts) - 1) / (ts[-1] - ts[0])
            return 0.0

    @property
    def rssi(self) -> Optional[int]:
        f = self._latest
        return f.rssi if f else None

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self._source.close()
        except Exception:
            pass


if __name__ == "__main__":
    # Tiny smoke test: stream a few synthetic frames and report their shape.
    spec = sys.argv[1] if len(sys.argv) > 1 else "--demo"
    stream = CSIStream(make_source(spec)).start()
    print(f"Streaming from {stream.label} — Ctrl-C to stop.")
    try:
        time.sleep(2.0)
        frames = stream.drain()
        if frames:
            print(f"{len(frames)} frames in 2 s (~{stream.fps:.0f} fps), "
                  f"{len(frames[-1].amp)} subcarriers, rssi={frames[-1].rssi}")
        else:
            print("No frames — is the board streaming / the port correct?")
    finally:
        stream.stop()
