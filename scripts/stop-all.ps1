$ErrorActionPreference = "Continue"
$ProjectDirectory = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "use-portable-runtime.ps1") -ProjectDirectory $ProjectDirectory
. (Join-Path $PSScriptRoot "lib\process.ps1")

Write-Host "=== DG-LAB Safe Stop ==="
& py -3.13 (Join-Path $ProjectDirectory "vibration-control.py") --stop 2>$null | Out-Null
$feedbackState = Join-Path $ProjectDirectory "feedback-state.json"
if (Test-Path -LiteralPath $feedbackState) { Remove-Item -LiteralPath $feedbackState -Force }
Stop-TrackedProcess "AI Director" (Join-Path $ProjectDirectory "ai-director.pid")
Stop-TrackedProcess "DG-LAB Bridge" (Join-Path $ProjectDirectory "dglab.pid")
Stop-TrackedProcess "V4 Relay" (Join-Path $ProjectDirectory "relay.pid")
Stop-TrackedProcess "Camera Bridge" (Join-Path $ProjectDirectory "server.pid")
Stop-ListeningProcess "DG-LAB Bridge" 25363 @("node")
Stop-ListeningProcess "V4 Relay" 9998 @("bun")
Stop-ListeningProcess "Camera Bridge" 7777 @("node")
Write-Host "Stop completed."
