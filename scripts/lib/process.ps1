function Start-TrackedProcess {
    param(
        [string]$Name, [string]$FilePath, [string[]]$ArgumentList,
        [string]$PidFile, [switch]$Visible, [string]$StdOut, [string]$StdErr
    )
    $options = @{
        FilePath = $FilePath
        ArgumentList = $ArgumentList
        WorkingDirectory = $ProjectDirectory
        PassThru = $true
        WindowStyle = $(if ($Visible) { "Normal" } else { "Hidden" })
    }
    if ($StdOut) { $options.RedirectStandardOutput = $StdOut }
    if ($StdErr) { $options.RedirectStandardError = $StdErr }
    $process = Start-Process @options
    Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ascii
    Write-Host "  $Name PID=$($process.Id)"
}

function Stop-TrackedProcess {
    param([string]$Name, [string]$PidFile)
    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "${Name}: no wrapper PID file"
        return
    }
    $processId = 0
    $text = [string](Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ([int]::TryParse($text.Trim(), [ref]$processId)) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            Write-Host "${Name}: stopped wrapper PID=$processId"
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-ListeningProcess {
    param([string]$Name, [int]$Port, [string[]]$AllowedProcessNames)
    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    $listenerIds = foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match $pattern) { [int]$Matches[1] }
    }
    foreach ($listenerId in ($listenerIds | Select-Object -Unique)) {
        $process = Get-Process -Id $listenerId -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        if ($process.ProcessName -notin $AllowedProcessNames) {
            Write-Warning "${Name}: port $Port belongs to unexpected process $($process.ProcessName) PID=$listenerId; not stopped"
            continue
        }
        Stop-Process -Id $listenerId -Force
        Write-Host "${Name}: stopped listener PID=$listenerId port=$Port"
    }
}
