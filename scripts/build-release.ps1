param(
    [string]$OutputDirectory,
    [switch]$SkipRuntimes
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProjectDirectory = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReleaseRoot = Join-Path $ProjectDirectory "release"
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ReleaseRoot "tentacle-monster-roleplay-windows-x64"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$releaseRootFull = [System.IO.Path]::GetFullPath($ReleaseRoot)
if (-not $OutputDirectory.StartsWith($releaseRootFull + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "OutputDirectory must be inside $releaseRootFull"
}
$archivePath = "$OutputDirectory.zip"
if ((Test-Path -LiteralPath $OutputDirectory) -or (Test-Path -LiteralPath $archivePath)) {
    $archiveDirectory = Join-Path $ReleaseRoot "archive\replaced-$stamp"
    New-Item -ItemType Directory -Path $archiveDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $OutputDirectory) {
        Move-Item -LiteralPath $OutputDirectory -Destination (Join-Path $archiveDirectory (Split-Path -Leaf $OutputDirectory))
    }
    if (Test-Path -LiteralPath $archivePath) {
        Move-Item -LiteralPath $archivePath -Destination (Join-Path $archiveDirectory (Split-Path -Leaf $archivePath))
    }
}

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha256
    )
    if (Test-Path -LiteralPath $Destination) {
        $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -eq $Sha256.ToLowerInvariant()) {
            Write-Host "Using cached runtime: $(Split-Path -Leaf $Destination)"
            return
        }
        Remove-Item -LiteralPath $Destination -Force
    }

    $partial = "$Destination.download"
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    Write-Host "Downloading official runtime: $Url"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial
        $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $Sha256.ToLowerInvariant()) {
            throw "SHA-256 mismatch for $(Split-Path -Leaf $Destination): expected $Sha256, got $actual"
        }
        Move-Item -LiteralPath $partial -Destination $Destination
    } finally {
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    }
}

function Copy-DirectoryContents {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$rootFiles = @(
    ".gitignore", "LICENSE", "README.md", "requirements.txt",
    "package.json", "package-lock.json", "ai-config.json", "project-config.json",
    "ai-director.py", "dglab-bridge.mjs",
    "server.js", "vibration-control.py", "waveform-control.py",
    "waveform-library.default.json", "random-wave.py",
    "start.bat", "start-all.bat", "stop.bat", "stop-all.bat",
    "status.bat", "stop-random-wave.bat", "reset-options.bat",
    "manage-waveforms.bat"
)
foreach ($name in $rootFiles) {
    $source = Join-Path $ProjectDirectory $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $OutputDirectory $name)
    }
}
$defaultWaveformLibrary = Join-Path $ProjectDirectory "waveform-library.default.json"
Copy-Item -LiteralPath $defaultWaveformLibrary -Destination (Join-Path $OutputDirectory "waveform-library.json")

# Enumerate text files so Windows PowerShell 5 does not need to parse
# non-ASCII file-name literals from this script.
Get-ChildItem -LiteralPath $ProjectDirectory -File -Filter "*.txt" | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $OutputDirectory $_.Name)
}

$sourceDirectories = @("scripts", "public", "legacy\esp32", "director", "tests", "waveforms")
foreach ($directory in $sourceDirectories) {
    $sourceRoot = Join-Path $ProjectDirectory $directory
    Get-ChildItem -LiteralPath $sourceRoot -File -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($ProjectDirectory.Length).TrimStart("\")
        if ($relative -match "(^|\\)(build|__pycache__)(\\|$)") { return }
        if ($relative -match "^waveforms\\(duplicates|processed)\\") { return }
        if ($_.Extension -in @(".pyc", ".bin", ".elf", ".map")) { return }
        $target = Join-Path $OutputDirectory $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target
    }
}

$workspaceDirectory = Split-Path -Parent $ProjectDirectory
$bundledRelaySource = Join-Path $ProjectDirectory "dglab-websocket-server"
$externalRelaySource = Join-Path $workspaceDirectory "dglab-websocket-server"
$relaySource = if (Test-Path -LiteralPath $bundledRelaySource) {
    $bundledRelaySource
} else {
    $externalRelaySource
}
if (-not (Test-Path -LiteralPath $relaySource)) {
    throw "Cannot build a portable release: dglab-websocket-server was not found"
}
$relayTarget = Join-Path $OutputDirectory "dglab-websocket-server"
Get-ChildItem -LiteralPath $relaySource -File -Recurse | ForEach-Object {
    $relative = $_.FullName.Substring($relaySource.Length).TrimStart("\")
    if ($relative -match "(^|\\)(\.git|node_modules)(\\|$)") { return }
    if ($_.Name -match "\.(log|pid)$") { return }
    $target = Join-Path $relayTarget $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target
}

$utf8 = [System.Text.UTF8Encoding]::new($false)
$projectConfig = [ordered]@{
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
    feedback = [ordered]@{
        intensity_unit = "percent"
        default_channel = "A"
        max_duration_ms = 200000
        min_pulse_duty_percent = 70
        ai_response_timeout_seconds = 20
        reaction_observation_seconds = 2
        allowed_patterns = @("BUBBLE", "PULSE", "BREATHING", "GRADUALRUB", "RHYTHM")
        channels = [ordered]@{
            A = [ordered]@{ enabled = $true; label = "Channel A"; location = "unspecified"; max_intensity = 10 }
            B = [ordered]@{ enabled = $true; label = "Channel B"; location = "unspecified"; max_intensity = 10 }
        }
    }
} | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText((Join-Path $OutputDirectory "project-config.json"), $projectConfig, $utf8)
[System.IO.File]::WriteAllText((Join-Path $OutputDirectory "ai-director-state.json"), "[]", $utf8)

if (-not $SkipRuntimes) {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "The portable release currently supports Windows x64 only"
    }

    $cacheDirectory = Join-Path $ReleaseRoot ".runtime-cache"
    $extractDirectory = Join-Path $ReleaseRoot ".runtime-extract-$stamp"
    New-Item -ItemType Directory -Path $cacheDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $extractDirectory -Force | Out-Null
    try {
        $nodeArchive = Join-Path $cacheDirectory "node-v24.19.0-win-x64.zip"
        $pythonArchive = Join-Path $cacheDirectory "python-3.13.15-embed-amd64.zip"
        $bunArchive = Join-Path $cacheDirectory "bun-v1.3.14-windows-x64.zip"
        Get-VerifiedDownload -Url "https://nodejs.org/download/release/v24.19.0/node-v24.19.0-win-x64.zip" -Destination $nodeArchive -Sha256 "57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73"
        Get-VerifiedDownload -Url "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip" -Destination $pythonArchive -Sha256 "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
        Get-VerifiedDownload -Url "https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-windows-x64.zip" -Destination $bunArchive -Sha256 "0a0620930b6675d7ba440e81f4e0e00d3cfbe096c4b140d3fff02205e9e18922"

        $nodeExtract = Join-Path $extractDirectory "node"
        $pythonExtract = Join-Path $extractDirectory "python"
        $bunExtract = Join-Path $extractDirectory "bun"
        Expand-Archive -LiteralPath $nodeArchive -DestinationPath $nodeExtract
        Expand-Archive -LiteralPath $pythonArchive -DestinationPath $pythonExtract
        Expand-Archive -LiteralPath $bunArchive -DestinationPath $bunExtract

        $runtimeRoot = Join-Path $OutputDirectory "runtime"
        $nodeSource = Get-ChildItem -LiteralPath $nodeExtract -Directory | Select-Object -First 1
        $bunExecutable = Get-ChildItem -LiteralPath $bunExtract -File -Filter "bun.exe" -Recurse | Select-Object -First 1
        if ($null -eq $nodeSource -or $null -eq $bunExecutable) {
            throw "A runtime archive has an unexpected directory layout"
        }
        Copy-DirectoryContents $nodeSource.FullName (Join-Path $runtimeRoot "node")
        Copy-DirectoryContents $pythonExtract (Join-Path $runtimeRoot "python")
        # The embeddable Python distribution runs in isolated mode and otherwise
        # cannot import the project's director package from two levels above.
        $pythonPathFile = Get-ChildItem -LiteralPath (Join-Path $runtimeRoot "python") -File -Filter "python*._pth" | Select-Object -First 1
        if ($null -eq $pythonPathFile) {
            throw "Python embeddable path configuration was not found"
        }
        $pythonPathLines = [System.IO.File]::ReadAllLines($pythonPathFile.FullName)
        if ($pythonPathLines -notcontains "..\..") {
            [System.IO.File]::AppendAllText($pythonPathFile.FullName, "..\..`r`n", [System.Text.Encoding]::ASCII)
        }
        New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "bun") -Force | Out-Null
        Copy-Item -LiteralPath $bunExecutable.FullName -Destination (Join-Path $runtimeRoot "bun\bun.exe")

        $binDirectory = Join-Path $runtimeRoot "bin"
        New-Item -ItemType Directory -Path $binDirectory -Force | Out-Null
        $pyWrapper = "@echo off`r`n`"`%~dp0..\python\python.exe`" `"`%~dp0py-launcher.py`" `%*`r`nexit /b `%errorlevel%`r`n"
        [System.IO.File]::WriteAllText((Join-Path $binDirectory "py.cmd"), $pyWrapper, [System.Text.Encoding]::ASCII)
        $pyLauncher = @"
import subprocess
import sys

arguments = sys.argv[1:]
if arguments and arguments[0].startswith("-3."):
    arguments.pop(0)
raise SystemExit(subprocess.run([sys.executable, *arguments]).returncode)
"@
        [System.IO.File]::WriteAllText((Join-Path $binDirectory "py-launcher.py"), $pyLauncher, $utf8)

        $npm = Join-Path $runtimeRoot "node\npm.cmd"
        Write-Host "Installing production npm dependencies into the release..."
        Push-Location $OutputDirectory
        try {
            & $npm install --omit=dev --ignore-scripts --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm dependency installation failed with exit code $LASTEXITCODE" }
        } finally {
            Pop-Location
        }

        $notices = @"
# Third-party runtimes

This Windows x64 package contains these unmodified upstream runtime distributions:

- Node.js 24.19.0 (including npm): https://nodejs.org/download/release/v24.19.0/
- Python 3.13.15 embeddable package: https://www.python.org/downloads/release/python-31315/
- Bun 1.3.14: https://github.com/oven-sh/bun/releases/tag/bun-v1.3.14

Their license files are included in their runtime directories where supplied by the upstream archives.
Project JavaScript dependency licenses remain in node_modules.
"@
        [System.IO.File]::WriteAllText((Join-Path $OutputDirectory "THIRD_PARTY_NOTICES.md"), $notices, $utf8)
    } finally {
        $extractFull = [System.IO.Path]::GetFullPath($extractDirectory)
        if ($extractFull.StartsWith($releaseRootFull + [System.IO.Path]::DirectorySeparatorChar) -and
            (Test-Path -LiteralPath $extractFull)) {
            Remove-Item -LiteralPath $extractFull -Recurse -Force
        }
    }
}

Compress-Archive -LiteralPath $OutputDirectory -DestinationPath $archivePath -CompressionLevel Optimal

Write-Host "Release directory: $OutputDirectory"
Write-Host "Release archive  : $archivePath"
