param(
    [Parameter(Mandatory = $true)][string]$ProjectDirectory,
    [Parameter(Mandatory = $true)][string]$WebSocketUrl,
    [Parameter(Mandatory = $true)][ValidateRange(1, 200)][int]$MaxIntensity
)
$ErrorActionPreference = "Stop"
$env:DGLAB_WS_URL = $WebSocketUrl
$env:DGLAB_MAX_INTENSITY = [string]$MaxIntensity
$env:DGLAB_PROJECT_CONFIG_FILE = Join-Path $ProjectDirectory "project-config.json"
Set-Location -LiteralPath $ProjectDirectory
& node .\dglab-bridge.mjs
exit $LASTEXITCODE
