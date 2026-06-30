"""Motion detector for WiFi Space Mapper — the proven pipeline as a reusable class.

This is the exact detector that live_csi_plot.py validated on hardware, lifted out
of the plotting loop so both the simple matplotlib viewer and the PyQtGraph
visualizer share one implementation (and so it can be unit-tested headlessly).

Why each stage exists (each defeats a specific way the naive "threshold the
variance" detector fails):

  1. Gain removal  — divide each frame by its own mean. The ESP32's AGC scales the
     whole amplitude vector per packet; dividing it out leaves only the channel
     *shape*, so gain wobble doesn't look like motion. (The #1 false-positive source.)
  2. Moving-window std (not frame-diff) — over a ~2 s window, measure how much each
     subcarrier's normalized amplitude churns. Still -> near-flat -> low; motion -> high.
  3. Calibrated threshold — learn the quiet "still" level for ~8 s at startup and FIX
     the threshold at P95(baseline) x 1.4, so continuous motion can't drag it upward.
  4. Hysteresis — separate enter/exit levels so the MOTION/STILL readout doesn't chatter.

Rate independence: the window and calibration are defined in SECONDS and converted to
frame counts using the fps measured live during warmup, so the detector behaves the
same at 22 fps or 90 fps. Measuring fps honestly needs two tricks: skip the sparse
boot/Wi-Fi-reconnect ramp, then average over a multi-second window (USB delivers
frames in ~16 ms bursts, so any short estimate lies).

Usage:
    det = MotionDetector()
    for frame in frames:
        st = det.update(frame.amp, frame.t)
        # st.phase in {"warmup","calibrate","detect"}; st.in_motion is the answer
"""

import time
from collections import deque, Counter
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Defaults are the hardware-validated values from live_csi_plot.py.
WARMUP_SETTLE_FPS = 15     # treat the stream as "up" once it exceeds this (skips boot ramp)
WARMUP_MEASURE_SEC = 3.0   # then measure the true fps over this window (averages USB bursts)
WINDOW_SEC = 2.0           # moving-std window duration
CALIB_SEC = 8.0            # still-baseline duration (STAY STILL this long)
THRESH_MULT = 1.4          # threshold = P95(still motion) * this
HYST_LOW = 0.6             # exit-motion level = HYST_LOW * threshold


@dataclass
class DetectorState:
    """A snapshot returned by every update() — everything a UI needs to render."""
    phase: str                       # "warmup" | "calibrate" | "detect"
    in_motion: bool                  # the MOTION/STILL answer (detect phase)
    motion_level: Optional[float]    # current moving-window churn, or None until the window fills
    threshold: Optional[float]       # fixed once calibrated
    fps: Optional[float]             # measured during warmup
    target_len: Optional[int]        # locked subcarrier count
    calib_progress: float            # 0..1 during calibration


class MotionDetector:
    def __init__(self, window_sec=WINDOW_SEC, calib_sec=CALIB_SEC,
                 thresh_mult=THRESH_MULT, hyst_low=HYST_LOW,
                 warmup_settle_fps=WARMUP_SETTLE_FPS,
                 warmup_measure_sec=WARMUP_MEASURE_SEC):
        self.window_sec = window_sec
        self.calib_sec = calib_sec
        self.thresh_mult = thresh_mult
        self.hyst_low = hyst_low
        self.warmup_settle_fps = warmup_settle_fps
        self.warmup_measure_sec = warmup_measure_sec

        # --- warmup state ---
        self.phase = "warmup"
        self._len_votes = Counter()
        self._fps_times = deque(maxlen=240)
        self._measure_start = None
        self._measure_count = 0

        # --- locked-in after warmup ---
        self.fps_locked = None
        self.target_len = None
        self._window_n = None
        self._calib_n = None
        self._shapes = None            # recent normalized shapes (deque sized once fps known)

        # --- calibrate / detect state ---
        self._baseline = []
        self.threshold = None
        self.motion_state = False      # False = still, True = motion (hysteresis)
        self._last_motion = None

    def _snapshot(self):
        calib = 0.0
        if self._calib_n:
            calib = min(1.0, len(self._baseline) / self._calib_n)
        return DetectorState(
            phase=self.phase,
            in_motion=self.motion_state,
            motion_level=self._last_motion,
            threshold=self.threshold,
            fps=self.fps_locked,
            target_len=self.target_len,
            calib_progress=calib,
        )

    def update(self, amp, t=None) -> DetectorState:
        """Feed one amplitude vector; returns the current DetectorState."""
        if t is None:
            t = time.time()
        self._fps_times.append(t)

        # --- WARMUP: measure fps + lock the dominant frame length ---
        if self.phase == "warmup":
            self._len_votes[len(amp)] += 1
            if self._measure_start is None:
                recent_1s = sum(1 for ts in self._fps_times if ts >= t - 1.0)
                if recent_1s >= self.warmup_settle_fps:   # stream is up — start measuring
                    self._measure_start, self._measure_count = t, 0
                return self._snapshot()
            self._measure_count += 1
            if t - self._measure_start >= self.warmup_measure_sec:
                self.fps_locked = self._measure_count / (t - self._measure_start)
                self.target_len = self._len_votes.most_common(1)[0][0]
                self._window_n = max(8, round(self.window_sec * self.fps_locked))
                self._calib_n = max(20, round(self.calib_sec * self.fps_locked))
                self._shapes = deque(maxlen=self._window_n)
                self.phase = "calibrate"
            return self._snapshot()

        # Ignore off-type frames so the window stays consistent.
        if len(amp) != self.target_len:
            return self._snapshot()

        # Gain removal: keep only the channel shape, not the AGC level.
        shape = amp / (amp.mean() + 1e-6)
        self._shapes.append(shape)
        if len(self._shapes) < self._window_n:
            return self._snapshot()

        # Motion level = how much each subcarrier wiggles over the window.
        arr = np.array(self._shapes)                 # (window_n, target_len)
        motion = float(arr.std(axis=0).mean())
        self._last_motion = motion

        # --- CALIBRATE: collect the still baseline, then fix the threshold ---
        if self.phase == "calibrate":
            self._baseline.append(motion)
            if len(self._baseline) >= self._calib_n:
                self.threshold = max(
                    float(np.percentile(self._baseline, 95) * self.thresh_mult), 1e-4)
                self.phase = "detect"
            return self._snapshot()

        # --- DETECT: hysteresis state machine ---
        if self.motion_state and motion < self.threshold * self.hyst_low:
            self.motion_state = False
        elif (not self.motion_state) and motion > self.threshold:
            self.motion_state = True
        return self._snapshot()

    def recalibrate(self):
        """Re-learn the still baseline (e.g. after moving the board or changing rooms).

        Keeps the already-measured fps and locked frame length; only the baseline
        and threshold are re-derived. Call this, then hold still for ~calib_sec.
        """
        if self.target_len is None:        # never finished warmup — nothing to keep
            return
        self._baseline = []
        self.threshold = None
        self.motion_state = False
        self._shapes = deque(maxlen=self._window_n)
        self.phase = "calibrate"
