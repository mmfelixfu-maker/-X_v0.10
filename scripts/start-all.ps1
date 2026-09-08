param(
    [ValidateRange(1, 200)][int]$MaxIntensity = 10,
    [ValidateRange(1, 720)][int]$AiMaxRounds = 360,
    [ValidateRange(10, 3600)][int]$AiIntervalSeconds = 10,
    [ValidateRange(1, 120)][int]$AiMaxMinutes = 60,
    [ValidateRange(10, 3600)][int]$MaxFrameAgeSeconds = 120,
    [switch]$NoAi,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "use-portable-runtime.ps1") -ProjectDirectory $ProjectDirectory
. (Join-Path $PSScriptRoot "lib\settings.ps1")
. (Join-Path $PSScriptRoot "lib\network.ps1")
. (Join-Path $PSScriptRoot "lib\process.ps1")
$WorkspaceDirectory = Split-Path -Parent $ProjectDirectory
$BundledRelayDirectory = Join-Path $ProjectDirectory "dglab-websocket-server"
$ExternalRelayDirectory = Join-Path $WorkspaceDirectory "dglab-websocket-server"
$RelayDirectory = if (Test-Path -LiteralPath $BundledRelayDirectory) {
    $BundledRelayDirectory
} else {
    $ExternalRelayDirectory
}
$ProjectConfigFile = Join-Path $ProjectDirectory "project-config.json"
$AiConfigFile = Join-Path $ProjectDirectory "ai-config.json"
$ProviderSettings = Get-AiProviderSettings -ConfigFile $AiConfigFile
$ProviderApiKey = [Environment]::GetEnvironmentVariable($ProviderSettings.ApiKeyEnv, "Process")
$FeedbackDeviceLocation = "unspecified"
$GameStyle = "default"
$StoryPacing = "slow"
$DefaultFeedbackChannel = "A"
$ChannelAEnabled = $true
$ChannelBEnabled = $true
$ChannelAMaxIntensity = $MaxIntensity
$ChannelBMaxIntensity = $MaxIntensity
$ChannelALocation = "unspecified"
$ChannelBLocation = "unspecified"
$FeedbackBurstRounds = 3
$FeedbackRestRounds = 1
$MaxEffectDurationMs = 5000
$MinPulseDutyPercent = 70
$AiResponseTimeoutSeconds = 20
$ReactionObservationSeconds = 2

if (Test-Path -LiteralPath $ProjectConfigFile) {
    $startupConfig = Get-Content -LiteralPath $ProjectConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $PSBoundParameters.ContainsKey("MaxIntensity") -and $null -ne $startupConfig.max_intensity) {
        $MaxIntensity = [int]$startupConfig.max_intensity
    }
    if (-not $PSBoundParameters.ContainsKey("AiMaxRounds") -and $null -ne $startupConfig.ai_max_rounds) {
        $AiMaxRounds = [int]$startupConfig.ai_max_rounds
    }
    if (-not $PSBoundParameters.ContainsKey("AiIntervalSeconds") -and $null -ne $startupConfig.ai_interval_seconds) {
        $AiIntervalSeconds = [int]$startupConfig.ai_interval_seconds
    }
    if (-not $PSBoundParameters.ContainsKey("AiMaxMinutes") -and $null -ne $startupConfig.ai_max_minutes) {
        $AiMaxMinutes = [int]$startupConfig.ai_max_minutes
    }
    if (-not $PSBoundParameters.ContainsKey("MaxFrameAgeSeconds") -and $null -ne $startupConfig.max_frame_age_seconds) {
        $MaxFrameAgeSeconds = [int]$startupConfig.max_frame_age_seconds
    }
    if (-not $PSBoundParameters.ContainsKey("NoAi") -and $startupConfig.start_ai -eq $false) {
        $NoAi = $true
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$startupConfig.feedback_device_location)) {
        $FeedbackDeviceLocation = [string]$startupConfig.feedback_device_location
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$startupConfig.game_style)) {
        $GameStyle = [string]$startupConfig.game_style
    }
    if ([string]$startupConfig.story_pacing -in @("slow", "normal", "fast")) {
        $StoryPacing = [string]$startupConfig.story_pacing
    }
    if ($null -ne $startupConfig.feedback_burst_rounds) {
        $FeedbackBurstRounds = [int]$startupConfig.feedback_burst_rounds
    }
    if ($null -ne $startupConfig.feedback_rest_rounds) {
        $FeedbackRestRounds = [int]$startupConfig.feedback_rest_rounds
    }
    if ($null -ne $startupConfig.feedback.max_duration_ms) {
        $MaxEffectDurationMs = [int]$startupConfig.feedback.max_duration_ms
    }
    if ($null -ne $startupConfig.feedback.min_pulse_duty_percent) {
        $MinPulseDutyPercent = [int]$startupConfig.feedback.min_pulse_duty_percent
    }
    if ($null -ne $startupConfig.feedback.ai_response_timeout_seconds) {
        $AiResponseTimeoutSeconds = [int]$startupConfig.feedback.ai_response_timeout_seconds
    }
    if ($null -ne $startupConfig.feedback.reaction_observation_seconds) {
        $ReactionObservationSeconds = [int]$startupConfig.feedback.reaction_observation_seconds
    }
    if ($null -ne $startupConfig.feedback.channels) {
        if ([string]$startupConfig.feedback.default_channel -in @("A", "B")) {
            $DefaultFeedbackChannel = [string]$startupConfig.feedback.default_channel
        }
        if ($null -ne $startupConfig.feedback.channels.A) {
            $ChannelAEnabled = [bool]$startupConfig.feedback.channels.A.enabled
            $ChannelAMaxIntensity = [int]$startupConfig.feedback.channels.A.max_intensity
            $ChannelALocation = [string]$startupConfig.feedback.channels.A.location
        }
        if ($null -ne $startupConfig.feedback.channels.B) {
            $ChannelBEnabled = [bool]$startupConfig.feedback.channels.B.enabled
            $ChannelBMaxIntensity = [int]$startupConfig.feedback.channels.B.max_intensity
            $ChannelBLocation = [string]$startupConfig.feedback.channels.B.location
        }
    } else {
        $ChannelAMaxIntensity = $MaxIntensity
        $ChannelBMaxIntensity = $MaxIntensity
        $ChannelALocation = $FeedbackDeviceLocation
    }
}

if ($MaxIntensity -notin 1..200) { throw "max_intensity must be 1..200" }
if ($AiMaxRounds -lt 1 -or $AiMaxRounds -gt 720) { throw "ai_max_rounds must be 1..720" }
if ($AiIntervalSeconds -lt 10 -or $AiIntervalSeconds -gt 3600) { throw "ai_interval_seconds must be 10..3600" }
if ($AiMaxMinutes -lt 1 -or $AiMaxMinutes -gt 120) { throw "ai_max_minutes must be 1..120" }
if ($MaxFrameAgeSeconds -lt 10 -or $MaxFrameAgeSeconds -gt 3600) { throw "max_frame_age_seconds must be 10..3600" }
if ($FeedbackBurstRounds -lt 1 -or $FeedbackBurstRounds -gt 20) { throw "feedback_burst_rounds must be 1..20" }
if ($FeedbackRestRounds -lt 1 -or $FeedbackRestRounds -gt 10) { throw "feedback_rest_rounds must be 1..10" }
if ($MaxEffectDurationMs -lt 100 -or $MaxEffectDurationMs -gt 600000) { throw "max_effect_duration_ms must be 100..600000" }
if ($MinPulseDutyPercent -lt 10 -or $MinPulseDutyPercent -gt 100) { throw "min_pulse_duty_percent must be 10..100" }
if ($AiResponseTimeoutSeconds -lt 3 -or $AiResponseTimeoutSeconds -gt 120) { throw "ai_response_timeout_seconds must be 3..120" }
if ($ReactionObservationSeconds -lt 1 -or $ReactionObservationSeconds -gt 10) { throw "reaction_observation_seconds must be 1..10" }
if ($StoryPacing -notin @("slow", "normal", "fast")) { throw "story_pacing must be slow, normal, or fast" }

if (-not $NonInteractive) {
    Write-Host "Available AI providers: $($ProviderSettings.Available -join ', ')"
    $providerText = Read-Host "AI provider (default: $($ProviderSettings.Name))"
    if (-not [string]::IsNullOrWhiteSpace($providerText)) {
        $ProviderSettings = Set-ActiveAiProvider -ConfigFile $AiConfigFile -ProviderName $providerText.Trim().ToLowerInvariant()
        $ProviderApiKey = [Environment]::GetEnvironmentVariable($ProviderSettings.ApiKeyEnv, "Process")
    }
    while ($true) {
        $keyPrompt = if ($ProviderApiKey) {
            "$($ProviderSettings.Label) API Key (blank keeps the current key)"
        } else {
            "$($ProviderSettings.Label) API Key (blank skips AI)"
        }
        $enteredKey = Read-SecretText -Prompt $keyPrompt
        if (-not [string]::IsNullOrWhiteSpace($enteredKey)) {
            $ProviderApiKey = $enteredKey.Trim()
            [Environment]::SetEnvironmentVariable($ProviderSettings.ApiKeyEnv, $ProviderApiKey, "Process")
        }

        if (-not $ProviderApiKey) { break }
        Write-Host "Verifying $($ProviderSettings.Label) API Key (no model tokens are used)..."
        $previousErrorAction = $ErrorActionPreference
        try {
            # Native stderr is a non-terminating PowerShell error. Do not let
            # a DNS/API probe abort the complete one-click startup.
            $ErrorActionPreference = "Continue"
            $apiCheck = (& py -3.13 (Join-Path $ProjectDirectory "ai-director.py") --list-models 2>&1) -join " "
            $apiCheckExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($apiCheckExitCode -eq 0) {
            Write-Host "$($ProviderSettings.Label) API Key: verified"
            break
        }
        if ($apiCheck -match "HTTP (401|403)|Authentication Fails") {
            Write-Warning "$($ProviderSettings.Label) rejected this API Key. Enter a valid key."
            $ProviderApiKey = $null
            [Environment]::SetEnvironmentVariable($ProviderSettings.ApiKeyEnv, $null, "Process")
            continue
        }
        if ($apiCheck -match "Could not resolve host|Resolving timed out") {
            Write-Warning "DNS cannot resolve the selected AI API. Startup will continue; check DNS/network before the AI loop can work."
        } else {
            Write-Warning "Could not verify the API Key because of a network/API error; startup will continue and retry later."
        }
        break
    }

    $MaxIntensity = Read-IntegerSetting "Device maximum intensity" $MaxIntensity 1 200
    $ChannelAEnabled = Read-BooleanSetting "Enable channel A" $ChannelAEnabled
    $ChannelAMaxIntensity = Read-IntegerSetting "Channel A maximum intensity" $ChannelAMaxIntensity 1 $MaxIntensity
    $channelAText = Read-Host "Channel A feedback location (default: $ChannelALocation)"
    if (-not [string]::IsNullOrWhiteSpace($channelAText)) { $ChannelALocation = $channelAText.Trim() }
    $ChannelBEnabled = Read-BooleanSetting "Enable channel B" $ChannelBEnabled
    $ChannelBMaxIntensity = Read-IntegerSetting "Channel B maximum intensity" $ChannelBMaxIntensity 1 $MaxIntensity
    $channelBText = Read-Host "Channel B feedback location (default: $ChannelBLocation)"
    if (-not [string]::IsNullOrWhiteSpace($channelBText)) { $ChannelBLocation = $channelBText.Trim() }
    $defaultChannelText = Read-Host "Default feedback channel A or B (default: $DefaultFeedbackChannel)"
    if (-not [string]::IsNullOrWhiteSpace($defaultChannelText)) { $DefaultFeedbackChannel = $defaultChannelText.Trim().ToUpperInvariant() }
    if ($DefaultFeedbackChannel -notin @("A", "B")) { throw "Default feedback channel must be A or B" }
    if (-not $ChannelAEnabled -and -not $ChannelBEnabled) { throw "At least one feedback channel must be enabled" }
    if (($DefaultFeedbackChannel -eq "A" -and -not $ChannelAEnabled) -or ($DefaultFeedbackChannel -eq "B" -and -not $ChannelBEnabled)) {
        throw "Default feedback channel must be enabled"
    }
    $AiMaxRounds = Read-IntegerSetting "AI maximum rounds" $AiMaxRounds 1 720
    $AiIntervalSeconds = Read-IntegerSetting "AI interval seconds" $AiIntervalSeconds 10 3600
    $AiMaxMinutes = Read-IntegerSetting "AI maximum minutes" $AiMaxMinutes 1 120
    $MaxFrameAgeSeconds = Read-IntegerSetting "Maximum camera frame age seconds" $MaxFrameAgeSeconds 10 3600
    $FeedbackBurstRounds = Read-IntegerSetting "Feedback rounds before forced rest" $FeedbackBurstRounds 1 20
    $FeedbackRestRounds = Read-IntegerSetting "Forced rest rounds" $FeedbackRestRounds 1 10
    $durationMaximum = 600000
    $MaxEffectDurationMs = Read-IntegerSetting "Maximum feedback plan duration (ms)" $MaxEffectDurationMs 100 $durationMaximum
    $MinPulseDutyPercent = Read-IntegerSetting "Minimum AI pulse active percentage" $MinPulseDutyPercent 10 100
    $AiResponseTimeoutSeconds = Read-IntegerSetting "Stop feedback if AI response exceeds seconds" $AiResponseTimeoutSeconds 3 120
    $ReactionObservationSeconds = Read-IntegerSetting "Observe player again after feedback starts (seconds)" $ReactionObservationSeconds 1 10
    $locationText = Read-Host "Feedback device location (default: $FeedbackDeviceLocation)"
    if (-not [string]::IsNullOrWhiteSpace($locationText)) { $FeedbackDeviceLocation = $locationText.Trim() }
    $styleText = Read-Host "Game style: default or custom description (default: $GameStyle)"
    if (-not [string]::IsNullOrWhiteSpace($styleText)) { $GameStyle = $styleText.Trim() }
    if ($FeedbackDeviceLocation.Length -gt 200) { throw "feedback_device_location is too long (max 200)" }
    if ($GameStyle.Length -gt 500) { throw "game_style is too long (max 500)" }
    $pacingText = Read-Host "Story pacing slow, normal, or fast (default: $StoryPacing)"
    if (-not [string]::IsNullOrWhiteSpace($pacingText)) { $StoryPacing = $pacingText.Trim().ToLowerInvariant() }
    if ($StoryPacing -notin @("slow", "normal", "fast")) { throw "Story pacing must be slow, normal, or fast" }
    $startAiDefault = if ($NoAi) { "n" } else { "y" }
    $startAiText = Read-Host "Start AI automatically? [y/n] (default $startAiDefault)"
    if ([string]::IsNullOrWhiteSpace($startAiText)) { $startAiText = $startAiDefault }
    $NoAi = $startAiText -notmatch "^(y|yes)$"

    [ordered]@{
        version = 2
        max_intensity = $MaxIntensity
        start_ai = -not $NoAi
        ai_max_rounds = $AiMaxRounds
        ai_interval_seconds = $AiIntervalSeconds
        ai_max_minutes = $AiMaxMinutes
        max_frame_age_seconds = $MaxFrameAgeSeconds
        feedback_device_location = $FeedbackDeviceLocation
        game_style = $GameStyle
        story_pacing = $StoryPacing
        feedback_burst_rounds = $FeedbackBurstRounds
        feedback_rest_rounds = $FeedbackRestRounds
        feedback = [ordered]@{
            intensity_unit = "percent"
            default_channel = $DefaultFeedbackChannel
            max_duration_ms = $MaxEffectDurationMs
            min_pulse_duty_percent = $MinPulseDutyPercent
            ai_response_timeout_seconds = $AiResponseTimeoutSeconds
            reaction_observation_seconds = $ReactionObservationSeconds
            allowed_patterns = @("BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM")
            channels = [ordered]@{
                A = [ordered]@{ enabled = $ChannelAEnabled; label = "Channel A"; max_intensity = $ChannelAMaxIntensity; location = $ChannelALocation }
                B = [ordered]@{ enabled = $ChannelBEnabled; label = "Channel B"; max_intensity = $ChannelBMaxIntensity; location = $ChannelBLocation }
            }
        }
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ProjectConfigFile -Encoding UTF8
}

Write-Host "=== DG-LAB One-click Startup ==="
Write-Host "Project: $ProjectDirectory"

foreach ($command in @("node", "npm.cmd", "bun", "py")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $command"
    }
}
if (-not (Test-Path -LiteralPath $RelayDirectory)) {
    throw "Relay directory not found. Expected bundled path $BundledRelayDirectory or external path $ExternalRelayDirectory"
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectDirectory "node_modules\dglab-kit"))) {
    Write-Host "Installing project Node.js dependencies..."
    & npm.cmd install --omit=dev --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed. Check the network and run npm.cmd install manually."
    }
}

$network = Get-LanIPv4
if (-not $network) { throw "Cannot detect LAN IPv4. Check Wi-Fi or Ethernet." }
$lanIp = $network.Address
$relayUrl = "ws://${lanIp}:9998"
Write-Host "Network: $($network.Adapter) -> $lanIp"
Write-Host "Safety intensity cap: $MaxIntensity"

if (-not (Test-TcpPort -HostName "127.0.0.1" -Port 9998)) {
    Write-Host "[1/4] Starting V4 Relay..."
    Start-TrackedProcess -Name "V4 Relay" -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run-relay.ps1"), "-RelayDirectory", $RelayDirectory) `
        -PidFile (Join-Path $ProjectDirectory "relay.pid")
} else {
    Write-Host "[1/4] V4 Relay is already running"
    $relayPid = Get-ListeningPid -Port 9998
    if ($relayPid) {
        Set-Content -LiteralPath (Join-Path $ProjectDirectory "relay.pid") -Value $relayPid -Encoding ascii
    }
}
if (-not (Wait-TcpPort -HostName "127.0.0.1" -Port 9998)) { throw "V4 Relay did not listen on port 9998" }

$bridgeRuntimeFile = Join-Path $ProjectDirectory "dglab-runtime.json"
$channelConfigSignature = "$DefaultFeedbackChannel|$ChannelAEnabled|$ChannelAMaxIntensity|$ChannelALocation|$ChannelBEnabled|$ChannelBMaxIntensity|$ChannelBLocation|$MaxEffectDurationMs"
$bridgeConfigMatches = $false
if ((Test-TcpPort -HostName "127.0.0.1" -Port 25363) -and (Test-Path -LiteralPath $bridgeRuntimeFile)) {
    try {
        $bridgeRuntime = Get-Content -LiteralPath $bridgeRuntimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $bridgeConfigMatches = ($bridgeRuntime.relay_url -eq $relayUrl -and [int]$bridgeRuntime.max_intensity -eq $MaxIntensity -and [string]$bridgeRuntime.channel_config -eq $channelConfigSignature)
    } catch { $bridgeConfigMatches = $false }
}

if ((Test-TcpPort -HostName "127.0.0.1" -Port 25363) -and -not $bridgeConfigMatches) {
    Write-Host "[2/4] Restarting DG-LAB Bridge so current network and safety settings apply..."
    & py -3.13 (Join-Path $ProjectDirectory "vibration-control.py") --stop 2>$null | Out-Null
    $bridgePid = Get-ListeningPid -Port 25363
    if ($bridgePid) {
        & taskkill.exe /PID $bridgePid /T /F | Out-Null
        Start-Sleep -Milliseconds 750
    } else {
        throw "Port 25363 is active but its process could not be identified"
    }
}
if (-not $bridgeConfigMatches) {
    Write-Host "[2/4] Starting DG-LAB Bridge (pairing window stays open)..."
    Start-TrackedProcess -Name "DG-LAB Bridge" -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run-dglab.ps1"), "-ProjectDirectory", $ProjectDirectory, "-WebSocketUrl", $relayUrl, "-MaxIntensity", [string]$MaxIntensity) `
        -PidFile (Join-Path $ProjectDirectory "dglab.pid") -Visible
} else {
    Write-Host "[2/4] DG-LAB Bridge settings match; preserving the current pairing"
}
if (-not (Wait-TcpPort -HostName "127.0.0.1" -Port 25363)) { throw "DG-LAB Bridge did not listen on port 25363" }
if (-not $bridgeConfigMatches) {
    [ordered]@{ relay_url = $relayUrl; max_intensity = $MaxIntensity; channel_config = $channelConfigSignature } | ConvertTo-Json | Set-Content -LiteralPath $bridgeRuntimeFile -Encoding UTF8
}

if (-not (Test-TcpPort -HostName "127.0.0.1" -Port 7777)) {
    Write-Host "[3/4] Starting Camera Bridge..."
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDirectory "certs\cert.pem"))) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "generate-cert.ps1")
        if ($LASTEXITCODE -ne 0) { throw "HTTPS certificate generation failed" }
    }
    Start-TrackedProcess -Name "Camera Bridge" -FilePath "node.exe" `
        -ArgumentList @("server.js") -PidFile (Join-Path $ProjectDirectory "server.pid") `
        -StdOut (Join-Path $ProjectDirectory "server.log") `
        -StdErr (Join-Path $ProjectDirectory "server.err.log")
} else { Write-Host "[3/4] Camera Bridge is already running" }
if (-not (Wait-TcpPort -HostName "127.0.0.1" -Port 7777)) { throw "Camera Bridge did not listen on port 7777" }
if (-not $NonInteractive) {
    Write-Host ""
    Write-Host "Scan this camera URL with the phone, then tap Start in Safari:"
    & node (Join-Path $PSScriptRoot "show-url-qr.mjs") "https://${lanIp}:7777/"
}

$aiPidFile = Join-Path $ProjectDirectory "ai-director.pid"
$aiRunning = $false
$frameFile = Join-Path $ProjectDirectory "latest.jpg"
$frameFresh = (Test-Path -LiteralPath $frameFile) -and (((Get-Date) - (Get-Item -LiteralPath $frameFile).LastWriteTime).TotalSeconds -le $MaxFrameAgeSeconds)
$deviceStatusText = (& py -3.13 (Join-Path $ProjectDirectory "vibration-control.py") --status 2>$null) -join " "
$deviceReady = $deviceStatusText -match "ready=1"
if (-not $NoAi -and $ProviderApiKey -and -not $deviceReady -and -not $NonInteractive) {
    Write-Host "Pair the DG-LAB app/device using the QR code in the Bridge window."
    Write-Host "Waiting up to 120 seconds for device pairing..."
    $pairDeadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        Start-Sleep -Seconds 2
        $deviceStatusText = (& py -3.13 (Join-Path $ProjectDirectory "vibration-control.py") --status 2>$null) -join " "
        $deviceReady = $deviceStatusText -match "ready=1"
    } while (-not $deviceReady -and [DateTime]::UtcNow -lt $pairDeadline)
}
if (-not $NoAi -and $ProviderApiKey -and -not $frameFresh -and -not $NonInteractive) {
    Write-Host "Camera frame is stale. Open https://${lanIp}:7777/ on the phone and start upload."
    Write-Host "Waiting up to 60 seconds for a fresh frame..."
    $frameDeadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        Start-Sleep -Seconds 2
        $frameFresh = (Test-Path -LiteralPath $frameFile) -and (((Get-Date) - (Get-Item -LiteralPath $frameFile).LastWriteTime).TotalSeconds -le $MaxFrameAgeSeconds)
    } while (-not $frameFresh -and [DateTime]::UtcNow -lt $frameDeadline)
}
if (Test-Path -LiteralPath $aiPidFile) {
    $oldPid = [int](Get-Content -LiteralPath $aiPidFile -ErrorAction SilentlyContinue)
    $aiRunning = [bool]($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue))
    if (-not $aiRunning) { Remove-Item -LiteralPath $aiPidFile -Force -ErrorAction SilentlyContinue }
}

if ($NoAi) {
    Write-Host "[4/4] AI Director skipped by option"
} elseif (-not $ProviderApiKey) {
    Write-Warning "[4/4] $($ProviderSettings.ApiKeyEnv) is missing; AI Director was not started"
} elseif (-not $deviceReady) {
    Write-Warning "[4/4] DG-LAB app/device is not paired; AI Director was not started"
} elseif (-not $frameFresh) {
    Write-Warning "[4/4] Camera frame is missing or stale; AI Director was not started"
} elseif ($aiRunning) {
    Write-Host "[4/4] AI Director is already running PID=$oldPid"
} else {
    Write-Host "[4/4] Starting bounded AI loop..."
    Start-TrackedProcess -Name "AI Director" -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run-ai.ps1"), "-ProjectDirectory", $ProjectDirectory, "-MaxRounds", [string]$AiMaxRounds, "-MaxMinutes", [string]$AiMaxMinutes, "-IntervalSeconds", [string]$AiIntervalSeconds, "-MaxFrameAgeSeconds", [string]$MaxFrameAgeSeconds) `
        -PidFile $aiPidFile -Visible
}

Write-Host ""
Write-Host "=== Infrastructure checks passed ==="
Write-Host "Relay : $relayUrl"
Write-Host "Camera: https://${lanIp}:7777/"
Write-Host "Control: 127.0.0.1:25363"
$deviceStatus = (& py -3.13 (Join-Path $ProjectDirectory "vibration-control.py") --status 2>$null | Select-Object -Last 1)
if ($deviceStatus -match "ready=1") {
    Write-Host "Device: paired and ready"
} else {
    Write-Warning "Device bridge is online, but the app/device is not paired. Scan the QR code in the DG-LAB Bridge window."
}
if (-not $frameFresh) {
    Write-Warning "Camera server is online, but no fresh phone frame is arriving. Open the Camera URL and tap Start."
}
