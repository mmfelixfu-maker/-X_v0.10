param(
    [Parameter(Mandatory = $true)][string]$ProjectDirectory,
    [Parameter(Mandatory = $true)][int]$MaxRounds,
    [Parameter(Mandatory = $true)][int]$MaxMinutes,
    [Parameter(Mandatory = $true)][int]$IntervalSeconds,
    [Parameter(Mandatory = $true)][int]$MaxFrameAgeSeconds
)

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $ProjectDirectory
$arguments = @(
    "-3.13", "-u", ".\ai-director.py",
    "--execute", "--loop",
    "--max-rounds", [string]$MaxRounds,
    "--max-minutes", [string]$MaxMinutes,
    "--loop-interval", [string]$IntervalSeconds,
    "--max-frame-age", [string]$MaxFrameAgeSeconds
)
& py @arguments | Tee-Object -FilePath ".\ai-director.log"
exit $LASTEXITCODE
