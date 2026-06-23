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
Requires ESP-IDF v5.3.5. From an ESP-IDF-activated shell:
```
idf.py set-target esp32
idf.py -p COM3 flash monitor
```

## Host-side plotting (later stages)
```
pip install pyserial numpy matplotlib
```
