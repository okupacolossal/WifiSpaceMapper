# Changelog

All notable changes to the WiFi Space Mapper firmware are recorded here.
The project is built up in **proven stages** — each stage compiles, flashes, and
is verified on real hardware before the next one starts.

Hardware: 1× ESP32-WROOM-32 (CP2102 USB-UART) · SDK: ESP-IDF v5.5.4 · Host: Windows 11.

## [Env] — 2026-06-26 — New dev machine + ESP-IDF v5.5.4

Migrated the whole build environment to a fresh Windows 11 machine and brought
the toolchain up from scratch. No firmware logic changed — this is an
environment/tooling pass so the project builds and flashes on the new setup.

### Changed
- **ESP-IDF v5.3.5 → v5.5.4** (installed via the ESP-IDF Tools Windows installer
  into `C:\Espressif`, the newer EIM layout). The from-scratch firmware builds
  unchanged — it only calls stock `esp_wifi` / CSI / `esp_netif` / `esp_event`
  APIs, all stable across the 5.x line.
- **`idf-shell.bat` rewritten for the EIM layout.** The old launcher hardcoded
  `C:\Espressif\tools\Microsoft.v5.3.5.PowerShell_profile.ps1`, which the new
  installer no longer creates (hence `CommandNotFoundException` on a fresh
  machine). It now auto-detects the newest `esp-idf-v*` framework under
  `C:\Espressif` and **dot-sources** its `export.ps1`. Dot-sourcing matters:
  calling it with `&` runs it in a child scope and the `idf.py` function (plus
  PATH edits) vanish when it returns.
- **`sdkconfig` regenerated** by `idf.py set-target esp32` on 5.5.4 (large but
  purely auto-generated SOC-caps churn). `CONFIG_ESP_WIFI_CSI_ENABLED=y` is
  preserved — it comes from the tracked `sdkconfig.defaults`.

### Environment notes
- **COM port is COM9 on this machine** (was COM3 on the previous one). The CP210x
  USB-UART driver (Silicon Labs VCP) had to be installed before the board
  enumerated under *Ports (COM & LPT)* — flash/monitor with `-p COM9`.
- Host plotter deps (`pyserial`, `numpy`, `matplotlib`) reinstalled in system
  Python 3.11.
- `_setup/` (the downloaded CP210x driver) is gitignored — it's local machine
  setup, not part of the firmware.

### Verified on hardware/toolchain
- `idf.py build` clean on v5.5.4 → `wifi_csi.bin`, 0xb6b20 bytes (29% of the app
  partition free).

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
