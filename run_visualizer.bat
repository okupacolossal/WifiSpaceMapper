@echo off
REM One-click launcher for the WiFi Space Mapper CSI visualizer.
REM
REM   run_visualizer.bat              -> demo mode (synthetic data, no board needed)
REM   run_visualizer.bat COM9 921600  -> live ESP32 (use your board's COM port)
REM   run_visualizer.bat path\to\take.npz   -> replay a recorded capture
REM
REM Uses your system Python (NOT the ESP-IDF shell's Python). First time only:
REM   pip install -r requirements.txt
REM Close the ESP-IDF serial monitor first — only one program can hold the port.

cd /d "%~dp0"
python tools\wifi_visualizer.py %*
pause
