$ErrorActionPreference = "Continue"
$ProjectDirectory = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "use-portable-runtime.ps1") -ProjectDirectory $ProjectDirectory

function Test-TcpPort {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        return $task.Wait(500) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Show-TrackedProcess {
    param([string]$Name, [string]$PidFile)
    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "$Name PID: not tracked"
        return
    }
    $trackedId = [int](Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue)
    $process = Get-Process -Id $trackedId -ErrorAction SilentlyContinue
    Write-Host "$Name PID: $trackedId alive=$([bool]$process)"
}

Write-Host "=== DG-LAB Full Status ==="
Write-Host "Relay 9998: $(Test-TcpPort 9998)"
Write-Host "Control 25363: $(Test-TcpPort 25363)"
Write-Host "Camera 7777: $(Test-TcpPort 7777)"
Show-TrackedProcess "Relay" (Join-Path $ProjectDirectory "relay.pid")
Show-TrackedProcess "DG-LAB" (Join-Path $ProjectDirectory "dglab.pid")
Show-TrackedProcess "Camera" (Join-Path $ProjectDirectory "server.pid")
Show-TrackedProcess "AI" (Join-Path $ProjectDirectory "ai-director.pid")

$feedbackStateFile = Join-Path $ProjectDirectory "feedback-state.json"
if (Test-Path -LiteralPath $feedbackStateFile) {
    try {
        $feedbackState = Get-Content -LiteralPath $feedbackStateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $expires = if ($feedbackState.expires_at) {
            [DateTimeOffset]::FromUnixTimeSeconds([long]$feedbackState.expires_at).LocalDateTime
        } else { "-" }
        Write-Host "Feedback state: active=$($feedbackState.active) mode=$($feedbackState.mode) channel=$($feedbackState.channel) percent=$($feedbackState.intensity_percent) expires=$expires"
    } catch {
        Write-Host "Feedback state: unreadable"
    }
} else {
    Write-Host "Feedback state: inactive"
}

Write-Host ""
Write-Host "DG-LAB protocol status:"
$protocolStatus = & py -3.13 (Join-Path $ProjectDirectory "vibration-control.py") --status
$protocolStatus
if (($protocolStatus -join " ") -notmatch "ready=1") {
    Write-Warning "DG-LAB app/device is not paired. Scan the Bridge QR code."
}

Write-Host ""
if (Test-Path -LiteralPath (Join-Path $ProjectDirectory "latest.jpg")) {
    $image = Get-Item -LiteralPath (Join-Path $ProjectDirectory "latest.jpg")
    $ageSeconds = [math]::Round(((Get-Date) - $image.LastWriteTime).TotalSeconds)
    Write-Host "latest.jpg: $($image.Length) bytes; updated=$($image.LastWriteTime); age=${ageSeconds}s"
    if ($ageSeconds -gt 120) {
        Write-Warning "Phone camera upload is stale. Open the Camera URL and tap Start."
    }
} else {
    Write-Host "latest.jpg: missing"
}
$aiConfig = Get-Content -LiteralPath (Join-Path $ProjectDirectory "ai-config.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$activeProvider = [string]$aiConfig.active_provider
$apiKeyEnv = [string]$aiConfig.providers.$activeProvider.api_key_env
$apiKeyValue = [Environment]::GetEnvironmentVariable($apiKeyEnv, "Process")
Write-Host "AI provider: $activeProvider"
Write-Host "$apiKeyEnv`: $(if ($apiKeyValue) { 'set' } else { 'missing' })"

$errorLogs = @("server.err.log", "ai-director.err.log")
foreach ($name in $errorLogs) {
    $path = Join-Path $ProjectDirectory $name
    if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -gt 0) {
        Write-Host ""
        Write-Host "Last errors from ${name}:"
        Get-Content -LiteralPath $path -Tail 10
    }
}
