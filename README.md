# WiFi Space Mapper

> **Device-free motion sensing from Wi-Fi radio reflections — on a commodity ESP32, built from scratch.**

![ESP-IDF](https://img.shields.io/badge/ESP--IDF-v5.5-E7352C)
![Firmware](https://img.shields.io/badge/firmware-C-00599C)
![Host](https://img.shields.io/badge/host-Python-3776AB)
![Hardware](https://img.shields.io/badge/hardware-ESP32--WROOM--32-222222)
![Domain](https://img.shields.io/badge/domain-RF%20sensing%20%2B%20DSP-6E4AFF)

A single ~$5 ESP32 turned into a **passive presence/motion sensor**. It reads the
**Channel State Information (CSI)** of ordinary Wi-Fi frames and detects when a
person moves through a room from how their body perturbs the signal's multipath —
**no camera, no PIR, no wearable.** Just how the radio waves bounce.

The whole capture stack is written **from scratch on ESP-IDF** — calling the
`esp_wifi` CSI API directly rather than wiring up a CSI library — because the goal
was to actually understand the RF + DSP pipeline end to end, not to drive a black box.

---

## TL;DR

| Layer | What it does |
|-------|--------------|
| **Firmware** (C / ESP-IDF) | Connects as a Wi-Fi station, enables CSI capture, **self-pings the gateway ~100×/s** to force a dense, regular frame stream, and prints each frame as CSV over a **921600-baud** serial link. |
| **Host** (Python) | Real-time DSP: amplitude → **gain removal** → **moving-window variance** → **still-calibrated adaptive threshold** → **hysteresis** → live **MOTION / STILL** readout. |
| **Result** | ~87 CSI frames/s on a single antenna; cleanly separates a still room from a person walking through the link *(working proof-of-concept).* |

---

## Why it's non-trivial — engineering highlights

This project is small in line count but dense in the kind of problems that don't
show up until you build the real thing:

- **CSI capture from first principles.** The entire capture is three driver calls
  (`esp_wifi_set_csi_config` / `_rx_cb` / `_csi(true)`) plus a callback that ships
  the raw I/Q buffer over serial. No vendor sensing SDK.

- **The *frequency* problem (data rate).** A connected Wi-Fi station only *receives*
  the router's beacons — roughly 10/s, irregularly spaced — and CSI only fires on
  received frames. That's far too sparse to compute a stable moving variance on.
  **Fix:** a self-ping traffic generator — the board pings its gateway on a tight
  interval, and every echo *reply* is a frame that traversed the room's multipath,
  firing the CSI callback. A serial-baud bump to 921600 keeps the dense stream from
  bottlenecking on the cable. Took the rate from ~7–10 fps to a *regular* ~23 fps.

- **Throughput optimization — a 4× more (data-rate).** Profiling the raw buffer
  revealed each packet carried **three redundant LTF blocks** (Legacy + 2× HT) —
  the same channel measured three times. Capturing only the Legacy LTF cut the
  payload 3× *and* made every frame a uniform 64 subcarriers (so the host could
  drop its frame-type-locking logic). With that headroom, tightening the ping
  interval to 10 ms lifted the stream from ~23 to **~87 fps** — measured, stable,
  and still well under the serial ceiling. (A telling result: the LTF cut alone
  didn't change fps, because the link was never the bottleneck — it raised the
  *ceiling* that the ping-rate change then pushed against.)

- **The *accuracy* problem (AGC).** The ESP32 re-adjusts its receive gain per packet,
  so the whole amplitude vector jumps for reasons unrelated to motion — the #1 reason
  naive CSI variance detectors fail. **Fix:** per-frame **gain removal**
  (divide by the frame mean) keeps only the channel *shape*, so gain wobble stops
  looking like motion. Motion is then measured as how much that shape *churns* over a
  sliding window — not a single frame-to-frame diff.

- **A threshold that can't cheat.** The detector learns the "still" noise floor during
  a short calibration window and **fixes** the threshold from it (P95 × 1.4), so a
  continuously moving target can't drag the threshold up to meet it. A hysteresis
  state machine (separate enter/exit levels) keeps the readout from flickering.

- **Rate-independent by design.** The detector's window and calibration are defined in
  *seconds*, then converted to frame counts from the frame rate it measures live at
  startup. So the same code behaves identically at 22 fps or 87 fps — change the capture
  rate and the detector's time constants don't silently shift underneath it. Measuring
  that rate honestly meant working around two hardware realities: the sparse
  Wi-Fi-reconnect ramp right after boot, and the ~16 ms *bursting* of USB-serial
  delivery, which makes any short-window rate estimate lie. The fix: wait until the
  stream is genuinely up, then count frames over a multi-second window.

---

## How it works

```
        2.4 GHz Wi-Fi                      USB serial (CSV @ 921600)
 ┌────────┐   ⇄   ┌─────────────┐   ────────────────────────────────►   ┌──────────────┐
 │ Router │  ⇄⇄⇄  │   ESP32     │   CSI_DATA,<len>,<rssi>,[I,Q,I,Q…]     │  Python host │
 └────────┘   ⇄   │  self-ping  │                                       │  detector    │
   reflections     └─────────────┘                                       └──────────────┘
   off the room   measures CSI on
   + the person   every RX frame

 Host pipeline:
   raw I/Q ─► |H| = √(I²+Q²) ─► gain removal (÷ mean) ─► moving-window std
           ─► P95 still-baseline threshold ─► hysteresis ─► MOTION / STILL
```

The host shows two live panels: the raw per-subcarrier amplitude (which visibly
jumps when you wave a hand) and the motion level over time against the threshold,
with a large MOTION / STILL banner.

---

## The signal — what CSI actually is

Wi-Fi (802.11n, 20 MHz) splits the channel into **64 OFDM subcarriers** — narrow
frequency slices. For every received frame, the radio estimates the **channel
response `H`** on each subcarrier by comparing the known training preamble against
what actually arrived. `H` is a complex number per subcarrier:

- **magnitude** `|H| = √(I² + Q²)` — how much that frequency was attenuated,
- **phase** `∠H = atan2(Q, I)` — how much it was delayed.

The ESP32 hands this back as interleaved signed `(imag, real)` byte pairs. A person
moving changes the reflections in the room, which changes these per-subcarrier
magnitudes — that's the whole physical basis of the sensor. (This project uses
**amplitude**; phase is noisier on a single radio without clock-sync tricks.)

**A detail you only learn by looking at the bytes:** with all training fields
enabled, each packet returns **192 values = 3 × 64** — the same channel measured
three times (Legacy LTF + two HT LTFs). Profiling the raw buffer made the structure
obvious: three repeating 64-bin blocks, each with a DC spike, two data sidebands, and
a run of zero-amplitude **guard subcarriers** at the band edges. Recognising that
redundancy is what enabled the 4× throughput win (capture one block, not three).

---

## The detector, step by step

The host runs a small real-time pipeline. Each stage exists to defeat a specific way
the naive "threshold the variance" approach fails:

1. **Amplitude** — `|H|` per subcarrier from the I/Q pairs.
2. **Gain removal** — divide the frame by its own mean. Cancels the ESP32's per-packet
   AGC, which otherwise scales the whole vector and masquerades as motion.
3. **Moving-window std** — over a ~2 s window, measure how much each subcarrier's
   normalized amplitude *churns*. Still room → near-flat → low; motion → high. (A
   window, not a frame-to-frame diff, so it reflects sustained change.)
4. **Calibrated threshold** — at startup, learn the quiet baseline for ~8 s and fix the
   threshold at `P95(baseline) × 1.4`. Fixing it means continuous motion can't drag it
   upward to meet itself.
5. **Hysteresis** — separate enter/exit levels so the MOTION/STILL readout doesn't
   chatter on the boundary.

All windows are expressed in **seconds** and scaled by the measured frame rate, so the
behaviour is identical across capture rates.

---

## Hardware

- **1× ESP32-WROOM-32** (CP2102 USB-UART) — classic dual-core ESP32, native Wi-Fi CSI.
- **A 2.4 GHz Wi-Fi router** as the ambient signal source (no router modification).
- A Windows/macOS/Linux host for the Python viewer.

---

## Build & flash

Requires **ESP-IDF v5.5.x** (developed on v5.5.4; also builds on v5.3.x — only stock
`esp_wifi` / CSI / `esp_netif` APIs are used, stable across the 5.x line).

**First time:** copy `main/secrets.example.h` → `main/secrets.h` and fill in your
2.4 GHz Wi-Fi SSID/password (`secrets.h` is gitignored, so credentials never get
committed).

On Windows, double-click **`idf-shell.bat`** for an ESP-IDF-activated shell already in
the project folder, then:

```bash
idf.py set-target esp32
idf.py -p COMx flash monitor       # COMx = your board's port
```

> **Find your COM port:** Device Manager → *Ports (COM & LPT)* → *Silicon Labs CP210x
> USB to UART Bridge (COMx)*. It varies per machine. The board needs the Silicon Labs
> **CP210x VCP driver** to appear there. On success the serial log shows
> `got ip: …` followed by `gateway ping started` and a stream of `CSI_DATA,…` lines.

---

## Run the motion detector

In a **regular** terminal (not the ESP-IDF shell — it uses a different Python),
with the serial monitor closed:

```bash
pip install pyserial numpy matplotlib
python tools/live_csi_plot.py            # defaults to COM9 @ 921600
python tools/live_csi_plot.py COM5 115200
```

Watch the title cycle through three phases:

1. **WARMUP** — measures the live frame rate and locks the CSI frame type.
2. **CALIBRATING — STAY STILL** (~8 s) — learns your room's quiet baseline. *Don't move.*
3. **DETECT** — green `still` / red `>>> MOTION DETECTED <<<`.

Then walk through the link and the motion line should cross the threshold and flip red.
The top panel also reports the live frames/sec.

---

## Results

Measured on a single ESP32 in a home room:

- **Frame rate:** ~87 CSI frames/s steady-state (single Legacy-LTF capture + 10 ms
  self-ping), up from ~23 fps — itself ~3× the beacon-only baseline.
- **Separation:** at ~100 fps the still-room motion level sits ≈ 0.03–0.07 against a
  threshold ≈ 0.10, with **zero false positives** over a still baseline run.
- **Detection:** a person walking through the link pushes the level well above the
  threshold and flips the state to MOTION.
- **Rate-independent:** the same detector calibrates and behaves consistently whether
  the board streams 22 or ~100 fps (windows scale with the measured rate).

It is an honest **proof-of-concept**: detection is reliable once calibrated for a given
room/geometry, and degrades if the environment or link changes after calibration. The
roadmap below is about hardening that.

---

## Scope & the physics that draws the lines

A single-antenna ESP32 on a 20 MHz channel has a range resolution of
`c / (2 × bandwidth) ≈ 7.5 m` and **no angle-of-arrival**, so it physically *cannot*
reconstruct a LiDAR-style floorplan of walls and furniture — that's a wave limit, not
a code limit. The realistic target is a **motion / occupancy** signal (and, with 3–4
nodes, a coarse heatmap of *where* activity is), not room geometry.

| Goal | Feasible on 1 board? |
|------|----------------------|
| Presence / motion detection | ✅ yes — *this project* |
| Activity discrimination (still / walking) | ✅ light feature work |
| Coarse localization (*where* the motion is) | 🟡 needs 3–4 nodes |
| Wall/furniture floorplan | 🔴 research-grade (SDR / antenna arrays) |

---

## Roadmap

- [x] **Rung 0** — stream CSI, live-plot subcarrier amplitude, react to a hand wave.
- [x] **Frequency pass** — self-ping + 921600 baud → dense, regular ~23 fps stream.
- [x] **Throughput optimization** — single Legacy-LTF capture (3× smaller, uniform
      frames) + 10 ms ping → ~87 fps.
- [x] **Rung 1 (PoC)** — gain-removal + moving-variance + calibrated-threshold detector.
- [x] **Rate-independent detector** — seconds-based windows scaled by the measured fps.
- [ ] **Robustness** — subcarrier selection (drop guard/DC), Hampel outlier filter,
      CV-based turbulence, P95 hysteresis hardening for cross-room generalization.
- [ ] **On-device gain lock** — ESPectre-style AGC/FFT gain locking in firmware, so the
      raw σ becomes a clean motion signal and detection can run on the ESP32 itself.
- [ ] **Rung 2** — activity discrimination (empty / still / walking) via a classifier.
- [ ] **Rung 3 (stretch)** — 3–4 nodes → coarse 2D motion heatmap.

---

## Repository layout

```
main/main.c            from-scratch firmware: STA connect → CSI capture → self-ping → CSV
main/secrets.example.h Wi-Fi credential template (copy to secrets.h)
tools/live_csi_plot.py host-side live viewer + motion detector
sdkconfig.defaults     CSI enabled + 921600 console baud (tracked build config)
idf-shell.bat          one-click ESP-IDF-activated shell (auto-detects the framework)
CHANGELOG.md           staged, hardware-verified build history
```

---

## Tech stack

**Embedded C** · **ESP-IDF v5.5** · **FreeRTOS** · **Wi-Fi CSI / 802.11 PHY** ·
**lwIP** (ICMP self-ping) · **Python** (NumPy, Matplotlib, pySerial) ·
**real-time DSP** (gain normalization, moving-window variance, adaptive thresholding,
hysteresis).

*Built incrementally in hardware-verified stages — see [`CHANGELOG.md`](CHANGELOG.md).*
