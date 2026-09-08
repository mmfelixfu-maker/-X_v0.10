@echo off
setlocal

cd /d "%~dp0"
set "PATH=%~dp0runtime\bin;%~dp0runtime\node;%~dp0runtime\bun;%~dp0runtime\python;%PATH%"
type nul > random-wave.stop
py .\vibration-control.py --stop

echo.
echo Random wave stop requested.
pause >nul
