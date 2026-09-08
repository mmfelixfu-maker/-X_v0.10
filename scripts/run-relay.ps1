param([Parameter(Mandatory = $true)][string]$RelayDirectory)
$ErrorActionPreference = "Stop"
$env:IDLE_TIMEOUT = "3600000"
$env:WS_PING_INTERVAL = "3600000"
$env:MAX_MISSED_WS_PONGS = "100"
Set-Location -LiteralPath $RelayDirectory
& bun run v4
exit $LASTEXITCODE
