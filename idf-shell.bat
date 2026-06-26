@echo off
REM Double-click (or run) this to open an ESP-IDF-activated PowerShell
REM already in the project folder. Then use: idf.py build / flash / monitor
REM
REM Auto-detects the newest ESP-IDF framework installed under C:\Espressif
REM (the EIM / "ESP-IDF Tools Windows" installer layout) and dot-sources its
REM export.ps1 so idf.py and the toolchain are on PATH for this session.
powershell -ExecutionPolicy Bypass -NoExit -Command ^
  "$fw=(Get-ChildItem 'C:\Espressif\frameworks' -Directory -Filter 'esp-idf-v*' | Sort-Object Name -Descending | Select-Object -First 1).FullName; $env:IDF_TOOLS_PATH='C:\Espressif'; $env:IDF_PATH=$fw; . \"$fw\export.ps1\"; Set-Location '%~dp0'"
