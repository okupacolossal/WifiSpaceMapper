"""Headless self-test for the CSI engine + detector — no hardware, no GUI.

Verifies the two pieces the visualizer depends on, using the synthetic source:
  1. the streaming engine parses frames into fixed-length amplitude vectors, and
  2. the motion detector calibrates a quiet baseline and then flips STILL->MOTION
     when motion is injected (the synthetic source goes still for 12 s, then
     alternates 4 s motion / 4 s still).

Run:  python tools/selftest_csi.py
Exit code 0 = pass. This is what CI / a quick "did I break it" check should run.
"""

import sys
import time

import numpy as np

from csi_stream import SyntheticSource, CSIStream, make_source
from detector import MotionDetector


def test_engine_parses_frames():
    src = SyntheticSource(fps=200.0)          # fast so the test is quick
    frames = []
    t0 = time.time()
    while time.time() - t0 < 1.0:
        f = src.read()
        if f is not None:
            frames.append(f)
        else:
            time.sleep(0.0005)
    src.close()
    assert len(frames) > 50, f"expected a steady stream, got {len(frames)} frames"
    lengths = {len(f.amp) for f in frames}
    assert len(lengths) == 1, f"frame length should be constant, saw {lengths}"
    assert all(np.all(f.amp >= 0) for f in frames), "amplitudes must be non-negative"
    print(f"  engine: {len(frames)} frames, {lengths.pop()} subcarriers, "
          f"rssi={frames[-1].rssi}  OK")


def test_detector_flips_on_motion():
    # Step a virtual clock so the seconds-based windows resolve instantly and
    # deterministically (no real-time waiting). The synthetic source is still for
    # the first 12 s, then alternates 4 s motion / 4 s still.
    fps = 90.0
    dt = 1.0 / fps
    src = SyntheticSource(fps=fps)
    det = MotionDetector()
    saw_calibrate = saw_detect = saw_motion = saw_still_in_detect = False
    threshold = None

    n = int(40.0 * fps)                       # 40 s of virtual time
    for i in range(n):
        elapsed = i * dt
        amp, _, _, _ = src.sample(elapsed)
        st = det.update(amp, t=src._t0 + elapsed)
        if st.phase == "calibrate":
            saw_calibrate = True
        elif st.phase == "detect":
            saw_detect = True
            threshold = st.threshold
            if st.in_motion:
                saw_motion = True
            else:
                saw_still_in_detect = True

    assert saw_calibrate, "detector never entered calibration"
    assert saw_detect, "detector never finished calibrating"
    assert threshold and threshold > 0, f"bad threshold {threshold}"
    assert saw_motion, "detector never flipped to MOTION when motion was injected"
    assert saw_still_in_detect, "detector never reported STILL (always-on = useless)"
    print(f"  detector: calibrated (threshold={threshold:.4f}), "
          f"separates STILL and MOTION  OK")


def test_make_source_dispatch():
    assert isinstance(make_source("--demo"), SyntheticSource)
    assert isinstance(make_source(None), SyntheticSource)
    print("  make_source: --demo -> SyntheticSource  OK")


def main():
    print("WiFi Space Mapper — engine + detector self-test")
    test_engine_parses_frames()
    test_detector_flips_on_motion()
    test_make_source_dispatch()
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
