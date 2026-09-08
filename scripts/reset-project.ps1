param(
    [ValidateSet("Settings", "Memory", "Waveforms", "All")]
    [string]$Mode,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "use-portable-runtime.ps1") -ProjectDirectory $ProjectDirectory
Set-Location -LiteralPath $ProjectDirectory

$pidFile = Join-Path $ProjectDirectory "ai-director.pid"
if (Test-Path -LiteralPath $pidFile) {
    $aiPid = 0
    $pidText = [string](Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $hasValidPid = [int]::TryParse($pidText.Trim(), [ref]$aiPid)
    if ($hasValidPid -and $aiPid -gt 0 -and (Get-Process -Id $aiPid -ErrorAction SilentlyContinue)) {
        throw "AI is still running. Run stop-all.bat before resetting."
    }
}

if (-not $Mode) {
    Write-Host "1. Reset startup/device settings"
    Write-Host "2. Reset AI story memory"
    Write-Host "3. Reset settings, story memory, and waveforms"
    Write-Host "4. Reset learned/imported waveforms"
    $selection = Read-Host "Select 1, 2, 3, or 4"
    $Mode = switch ($selection) {
        "1" { "Settings" }
        "2" { "Memory" }
        "3" { "All" }
        "4" { "Waveforms" }
        default { throw "Invalid selection" }
    }
}

if (-not $Force) {
    $answer = Read-Host "Reset $Mode? Existing files will be backed up first [y/N]"
    if ($answer -notmatch "^(y|yes)$") {
        Write-Host "Cancelled."
        exit 0
    }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDirectory = Join-Path $ProjectDirectory "reset-backups\$stamp"
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

function Backup-File {
    param([string]$Name)
    $source = Join-Path $ProjectDirectory $Name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $backupDirectory $Name) -Force
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText(
        $Path,
        $Content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

if ($Mode -in @("Settings", "All")) {
    Backup-File "project-config.json"
    $projectJson = @{
        version = 2
        max_intensity = 10
        start_ai = $true
        ai_max_rounds = 360
        ai_interval_seconds = 10
        ai_max_minutes = 60
        max_frame_age_seconds = 120
        feedback_device_location = "unspecified"
        game_style = "default"
        story_pacing = "slow"
        feedback_burst_rounds = 3
        feedback_rest_rounds = 1
        feedback = @{
            intensity_unit = "percent"
            default_channel = "A"
            max_duration_ms = 200000
            min_pulse_duty_percent = 70
            ai_response_timeout_seconds = 20
            reaction_observation_seconds = 2
            allowed_patterns = @("BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM")
            channels = @{
                A = @{ enabled = $true; label = "Channel A"; location = "unspecified"; max_intensity = 10 }
                B = @{ enabled = $true; label = "Channel B"; location = "unspecified"; max_intensity = 10 }
            }
        }
    } | ConvertTo-Json -Depth 5
    Write-Utf8NoBom (Join-Path $ProjectDirectory "project-config.json") $projectJson
}

if ($Mode -in @("Memory", "All")) {
    Backup-File "ai-director-state.json"
    Backup-File "ai-last-error.json"
    Backup-File "feedback-state.json"
    Write-Utf8NoBom (Join-Path $ProjectDirectory "ai-director-state.json") "[]"
    $badResponse = Join-Path $ProjectDirectory "ai-last-error.json"
    if (Test-Path -LiteralPath $badResponse) { Remove-Item -LiteralPath $badResponse -Force }
    $feedbackState = Join-Path $ProjectDirectory "feedback-state.json"
    if (Test-Path -LiteralPath $feedbackState) { Remove-Item -LiteralPath $feedbackState -Force }
}

if ($Mode -in @("Waveforms", "All")) {
    Backup-File "waveform-library.json"
    $defaultWaveforms = Join-Path $ProjectDirectory "waveform-library.default.json"
    if (-not (Test-Path -LiteralPath $defaultWaveforms)) {
        throw "Default waveform library is missing"
    }
    Copy-Item -LiteralPath $defaultWaveforms -Destination (Join-Path $ProjectDirectory "waveform-library.json") -Force
}

Write-Host "Reset complete: $Mode"
Write-Host "Backup: $backupDirectory"
