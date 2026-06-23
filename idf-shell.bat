@echo off
REM Double-click (or run) this to open an ESP-IDF-activated PowerShell
REM already in the project folder. Then use: idf.py build / flash / monitor
powershell -ExecutionPolicy Bypass -NoExit -Command ^
  "& 'C:\Espressif\tools\Microsoft.v5.3.5.PowerShell_profile.ps1'; Set-Location '%~dp0'"
