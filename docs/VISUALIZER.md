# CSI Visualizer — User Guide

A real-time desktop app that shows the Wi-Fi channel "move" in several ways at once
and runs the working motion detector — all on a single shared streaming engine.

> **Don't have an ESP32 handy?** Run it in **demo mode** (`--demo`) and the whole app
> works on believable synthetic CSI. Great for a first look or a screen recording.

---

## Install

Use your **system Python** (3.9+), *not* the ESP-IDF shell's bundled Python:

```bash
pip install -r requirements.txt
```

That pulls in `numpy`, `pyserial`, `matplotlib`, `pyqtgraph`, and `PySide6`.

---

## Run

```bash
# Demo — synthetic data, no hardware needed
python tools/wifi_visualizer.py
python tools/wifi_visualizer.py --demo

# Live — a connected ESP32 (use YOUR board's COM port; baud defaults to 921600)
python tools/wifi_visualizer.py COM9 921600     # laptop port in this project
python tools/wifi_visualizer.py COM3 921600     # desktop port in this project

# Replay — play back a capture recorded by tools/record_csi.py
python tools/wifi_visualizer.py data/20260628/gon_20260628_take01.npz
```

On Windows you can also just double-click **`run_visualizer.bat`** (defaults to demo;
pass a port to go live).

> **Close the ESP-IDF serial monitor first** — only one program can hold the COM port.
> Opening the port resets the board, so in live mode expect a few seconds of boot +
> Wi-Fi reconnect before frames start flowing.

---

## The views

Switch between them with the tabs along the top. All views read from the same live
stream, so the waterfall and motion history stay continuous as you switch tabs.

| Tab | What it shows | How to read it |
|-----|---------------|----------------|
| **Dashboard** | Raw amplitude + spectrogram + motion + a big banner, all at once. | The glance screen. Banner is green `still` / red `MOTION`. |
| **Raw CSI** | Per-subcarrier amplitude `\|H\|` of the current frame, with a fading trail of recent frames; plus an **I/Q constellation** (real vs imag). | Still room → the curve barely moves. Wave a hand → it jumps. The constellation is the complex channel per subcarrier. *(I/Q is unavailable for replay files — they store amplitude only.)* |
| **Spectrogram** | A scrolling waterfall: each column is one frame, rows are subcarriers, colour is amplitude. | Still → smooth horizontal streaks. Motion → the texture churns and flickers. This is the headline "waves moving" view. |
| **Motion** | The detector: motion level over time vs the calibrated threshold, with phase info and a **Recalibrate** button. | Line goes red and crosses the dashed threshold when you move. |
| **Radar** | Subcarriers placed around a circle, amplitude = radius. | A living, pulsing "flower." Its shape is the channel; motion makes it ripple. |
| **Doppler** | The FFT of the recent motion signal — its frequency content. | A peak around **0.5–10 Hz** is human motion; faster components are noise. |

---

## Controls & telemetry

- **Top telemetry bar:** connection dot (green = frames flowing), source label, live
  **fps**, **RSSI**, **subcarrier count**, and the detector **state**.
- **⏸ Pause / ▶ Resume:** freezes the display (the stream keeps running underneath).
- **⟳ Recalibrate:** re-learns the still baseline — use it after moving the board or
  changing rooms. Then **stay still ~8 s**.

---

## The detector, and its three phases

Watch the banner / state badge cycle through:

1. **WARMUP** — measures the live frame rate and locks the CSI frame length. (The rate
   is measured honestly: it waits out the sparse boot/reconnect ramp, then averages over
   a few seconds because USB delivers frames in ~16 ms bursts.)
2. **CALIBRATING — STAY STILL** (~8 s) — learns your room's quiet baseline and fixes the
   threshold from it (`P95 × 1.4`), so continuous motion can't drag the threshold upward.
3. **DETECT** — live `still` / `MOTION`, with hysteresis so the readout doesn't chatter.

All windows are defined in **seconds** and scaled by the measured fps, so the detector
behaves the same at 22 fps or ~90 fps.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Could not open COMx` | The ESP-IDF monitor (or another program) holds the port. Close it. Check the port number in Device Manager → *Ports (COM & LPT)*. |
| Window opens but **fps stays 0** / no frames | Wrong COM port, board not streaming, or wrong baud. The firmware console is **921600**. Confirm `CSI_DATA,…` lines appear in a serial monitor first. |
| Garbled in a serial monitor but fine here | Monitor baud mismatch (ROM boot is always 115200). Not an issue for this app — it opens at the baud you pass. |
| Serial open fails on launch | The app pops a warning and **falls back to demo mode** so you still get a window. |
| Laggy / high CPU | Lower the refresh by raising `refresh_ms` in `MainWindow`, or stay on a single tab (only the visible view repaints). |

---

## Architecture (for extending it)

Three small modules, one app:

```
tools/csi_stream.py   the engine: CSISource (Serial | Replay | Synthetic) + a threaded
                      CSIStream reader. Yields Frame(amp, rssi, t, imag, real).
tools/detector.py     MotionDetector — the proven pipeline as a reusable class.
tools/wifi_visualizer.py   the PyQtGraph app: a View per tab, fed a SharedState each tick.
```

- **Add a data source:** implement `read()` / `close()` (and a `label`) like the existing
  sources, then return it from `make_source()`.
- **Add a view:** subclass `View`, implement `update_view(state)`, and add it to the
  `self.views` list in `MainWindow`. `state` already carries the latest frame, the
  detector state, the waterfall buffer, and the motion history.

---

## A note on the preview images

The pictures in the README under `docs/media/` are rendered by `tools/make_previews.py`
from the **synthetic** source — representative, but not screenshots of the live GUI.
Replace them with real screen captures from a machine with a display and a board.
