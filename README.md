# WiFi Space Mapper

Passive **Wi-Fi sensing** on a commodity ESP32 — using Channel State Information (CSI)
to detect presence and motion in a room from how Wi-Fi signals reflect, scatter, and
get absorbed by people and objects. No cameras, no LiDAR.

Built **from scratch** on ESP-IDF (own `esp_wifi` CSI capture) — the goal is to learn
how the waves and the capture pipeline actually work, not to wire up a black box.

## Goal & scope

| Stage | Capability | Status |
|-------|-----------|--------|
| Rung 0 | Stream raw CSI, watch it react to motion | in progress |
| Rung 1 | Presence / motion detector (CSI variance) | planned |
| Rung 2 | Activity discrimination (empty / still / walking) | planned |
| Rung 3 | Coarse 2D motion heatmap (needs 3–4 nodes) | stretch |

> **Physics note:** a single-antenna ESP32 on a 20 MHz channel has ~7.5 m range
> resolution and no angle-of-arrival, so it **can't** reconstruct a LiDAR-style
> floorplan. The realistic target is a motion/occupancy heatmap, not room geometry.

## Hardware
- 1× ESP32-WROOM-32 (CP2102 USB-UART)
- A 2.4 GHz Wi-Fi router as the signal source

## Firmware build-up (each stage proven before the next)
- **Stage 0** — boot + serial print (skeleton) ✅
- **Stage 1** — Wi-Fi station: connect to the router, log the acquired IP
- **Stage 2** — CSI: register an `esp_wifi_set_csi_rx_cb` callback, stream CSI as CSV
- **Stage 3** — self-ping the gateway for a steady stream of frames to measure
- **Host** — Python (`pyserial` + `numpy` + `matplotlib`) live subcarrier-amplitude plot

## Build & flash
Requires **ESP-IDF v5.5.x** (developed on v5.5.4; also builds on v5.3.5 — only
stock `esp_wifi`/CSI/`esp_netif` APIs are used, stable across 5.x).

On Windows, double-click **`idf-shell.bat`** for an ESP-IDF-activated PowerShell
already in the project folder (it auto-detects the framework installed under
`C:\Espressif`). Then:
```
idf.py set-target esp32
idf.py -p COMx flash monitor          # COMx = your board's port — see below
```
> **Find your COM port:** Device Manager → *Ports (COM & LPT)* → *Silicon Labs
> CP210x USB to UART Bridge (COMx)*. It varies per machine (e.g. COM3 on one
> laptop, COM9 on another). The board needs the Silicon Labs **CP210x VCP
> driver** to appear here.

First time only: copy `main/secrets.example.h` → `main/secrets.h` and fill in
your 2.4 GHz Wi-Fi SSID/password (`secrets.h` is gitignored).

## Host-side plotting (later stages)
```
pip install pyserial numpy matplotlib
```
