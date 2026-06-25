# Changelog

All notable changes to the WiFi Space Mapper firmware are recorded here.
The project is built up in **proven stages** — each stage compiles, flashes, and
is verified on real hardware before the next one starts.

Hardware: 1× ESP32-WROOM-32 (CP2102 USB-UART) · SDK: ESP-IDF v5.3.5 · Host: Windows 11.

## [Stage 2] — 2026-06-25 — CSI capture + live host plotter

### Added
- CSI callback in `main/main.c`: `esp_wifi_set_csi_config()` /
  `esp_wifi_set_csi_rx_cb()` / `esp_wifi_set_csi(true)` after Wi-Fi start; the
  callback `printf`s each frame as `CSI_DATA,<len>,<rssi>,[bytes...]` over serial.
- `sdkconfig.defaults` with `CONFIG_ESP_WIFI_CSI_ENABLED=y`.
- `tools/live_csi_plot.py` — host plotter (pyserial + numpy + matplotlib): parses
  the CSV, computes per-subcarrier amplitude, shows the live raw shape plus a
  first-cut motion line. Drains the serial backlog to stay low-latency.

### Fixed / learned
- **CSI was crashing in a boot loop** (`ESP_FAIL` at `esp_wifi_set_csi_config`)
  until `CONFIG_ESP_WIFI_CSI_ENABLED=y` was enabled — the chip supports CSI
  (`SOC_WIFI_CSI_SUPPORT`) but the driver leaves it out by default.
- ESP32 emits variable CSI frame lengths per packet type; the plotter only diffs
  same-length frames.

### Known limitation (next milestone)
- The first home-grown motion detector (frame-diff of mean-normalized amplitude +
  median/MAD threshold) is inaccurate. A research-backed rebuild is planned:
  subcarrier selection → Hampel filter → turbulence (CV / gain-locked σ) → moving
  variance → P95 adaptive threshold → hysteresis (see the MYBRAIN project notes).

## [Stage 1] — 2026-06-25 — Wi-Fi station connects to the router

### Added
- `wifi_init_sta()` in `main/main.c`. In order:
  - initialises NVS (Wi-Fi stores radio calibration there);
  - brings up the TCP/IP stack (`esp_netif`) and the default event loop;
  - initialises the Wi-Fi driver and creates the default STA interface;
  - registers an event handler **before** starting the radio;
  - sets station mode + SSID/password and calls `esp_wifi_start()`.
- Event handler reacting to three events:
  - `WIFI_EVENT_STA_START` → `esp_wifi_connect()` (begin association);
  - `WIFI_EVENT_STA_DISCONNECTED` → reconnect (keeps an unattended sensor alive
    through transient drops; no retry limit yet);
  - `IP_EVENT_STA_GOT_IP` → logs the acquired IP (the success signal).
- `main/secrets.example.h` — template for Wi-Fi credentials.
- `.gitignore` rule for `main/secrets.h` so the real SSID/password are never
  committed to this public repo.

### Verified on hardware
- Board associates with the 2.4 GHz network and logs `got ip: 192.168.1.100`.
- Full chain observed in serial: `init → auth → assoc → run → got IP`
  (channel 11, WPA2-PSK, RSSI −78).

## [Stage 0] — 2026-06-23 — Project skeleton

### Added
- From-scratch ESP-IDF project (own `esp_wifi` CSI capture planned — `esp-csi`
  kept as a reference crib, not a dependency; the goal is to learn the pipeline).
- `main/main.c` boot + serial print, with the staged build-up plan in comments.
- Build system: `CMakeLists.txt`, `main/CMakeLists.txt`
  (`REQUIRES esp_wifi nvs_flash esp_netif esp_event lwip`).
- `idf-shell.bat` — launcher that activates the ESP-IDF environment and cd's in
  (the VS Code ESP-IDF extension config is broken on this machine; this is the
  reliable way to get an `idf.py` shell).
- `.vscode/` IntelliSense config; `README.md` with the rung roadmap.

### Verified on hardware
- Skeleton compiles, flashes, and prints over serial. Toolchain build→flash→serial
  loop proven with the `hello_world` example first.
