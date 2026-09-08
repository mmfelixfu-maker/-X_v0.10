@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PATH=%~dp0runtime\bin;%~dp0runtime\node;%~dp0runtime\bun;%~dp0runtime\python;%PATH%"

echo ==================================================
echo User waveform manager
echo Drop .pulse or .zip files into: waveforms\inbox
echo ==================================================
echo 1. Scan inbox and analyze/import waveforms
echo 2. Import a specified .pulse/.zip/JSON file
echo 3. List imported DungeonLab waveforms and analysis
echo 4. List AI-created/internal JSON waveforms
echo 5. Show import folders
set /p "choice=Select 1-5: "

if "%choice%"=="1" (
  py -3.13 .\waveform-control.py --scan-inbox
  goto done
)

if "%choice%"=="2" (
  set /p "importFile=File path (.pulse/.zip/.json): "
  py -3.13 .\waveform-control.py --import "%importFile%"
  goto done
)

if "%choice%"=="3" (
  py -3.13 .\waveform-control.py --list-pulses
  goto done
)

if "%choice%"=="4" (
  py -3.13 .\waveform-control.py --list
  goto done
)

if "%choice%"=="5" (
  echo Put new files here: %CD%\waveforms\inbox
  echo Default AI files:  %CD%\waveforms\default
  echo Rejected files:    %CD%\waveforms\rejected
  echo Analysis catalog:  %CD%\waveforms\catalog.json
  explorer.exe "%CD%\waveforms"
  goto done
)

echo Invalid selection.

:done
echo.
pause
