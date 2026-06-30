@echo off
REM Double-click (or run) this to open an ESP-IDF-activated PowerShell already in
REM the project folder. Then use: idf.py build / flash / monitor
REM
REM Activation, in order of reliability (handles both machines on this project):
REM   1. EIM PowerShell profile C:\Espressif\tools\Microsoft.v*.PowerShell_profile.ps1
REM      (names the version actually INSTALLED — avoids picking an uninstalled
REM       framework dir like a stray v6.0.1). This is the desktop's working path.
REM   2. Framework auto-detect C:\Espressif\frameworks\esp-idf-v*\export.ps1 (laptop).
REM   3. C:\esp\v*\esp-idf\export.ps1 as a last resort.
powershell -ExecutionPolicy Bypass -NoExit -Command ^
  "$p=Get-ChildItem 'C:\Espressif\tools\Microsoft.v*.PowerShell_profile.ps1' -ErrorAction SilentlyContinue ^| Sort-Object Name -Descending ^| Select-Object -First 1;" ^
  "if($p){ . $p.FullName; Set-Location '%~dp0' }" ^
  "elseif(Test-Path 'C:\Espressif\frameworks'){ $fw=(Get-ChildItem 'C:\Espressif\frameworks' -Directory -Filter 'esp-idf-v*' ^| Sort-Object Name -Descending ^| Select-Object -First 1).FullName; $env:IDF_TOOLS_PATH='C:\Espressif'; $env:IDF_PATH=$fw; . \"$fw\export.ps1\"; Set-Location '%~dp0' }" ^
  "else { $c=Get-ChildItem 'C:\esp' -Directory -Filter 'v*' -ErrorAction SilentlyContinue ^| Sort-Object Name -Descending ^| Select-Object -First 1; if($c){ $env:IDF_TOOLS_PATH='C:\Espressif\tools'; $env:IDF_PATH=Join-Path $c.FullName 'esp-idf'; . (Join-Path $c.FullName 'esp-idf\export.ps1'); Set-Location '%~dp0' } else { Write-Host 'ESP-IDF not found.' } }"
