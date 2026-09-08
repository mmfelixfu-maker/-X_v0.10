@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0runtime\bin;%~dp0runtime\node;%~dp0runtime\bun;%~dp0runtime\python;%PATH%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\reset-project.ps1"
pause
