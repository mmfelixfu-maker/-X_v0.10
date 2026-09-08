@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PATH=%~dp0runtime\bin;%~dp0runtime\node;%~dp0runtime\bun;%~dp0runtime\python;%PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-all.ps1"
echo.
pause
