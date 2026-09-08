function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 500)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($HostName, $Port)
        return $task.Wait($TimeoutMs) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Wait-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutSeconds = 20)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-TcpPort -HostName $HostName -Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Get-ListeningPid {
    param([int]$Port)
    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+\S+\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano -p tcp)) { if ($line -match $pattern) { return [int]$Matches[1] } }
    return $null
}

function Get-LanIPv4 {
    $candidates = foreach ($adapter in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
        if ($adapter.NetworkInterfaceType -in @(
            [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback,
            [System.Net.NetworkInformation.NetworkInterfaceType]::Tunnel
        )) { continue }
        $properties = $adapter.GetIPProperties()
        $hasGateway = @($properties.GatewayAddresses | Where-Object {
            $_.Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
            -not $_.Address.Equals([System.Net.IPAddress]::Any)
        }).Count -gt 0
        foreach ($entry in $properties.UnicastAddresses) {
            $address = $entry.Address
            if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { continue }
            if ([System.Net.IPAddress]::IsLoopback($address)) { continue }
            $text = $address.IPAddressToString
            if ($text -like "169.254.*") { continue }
            $typeScore = switch ($adapter.NetworkInterfaceType) {
                ([System.Net.NetworkInformation.NetworkInterfaceType]::Wireless80211) { 30 }
                ([System.Net.NetworkInformation.NetworkInterfaceType]::Ethernet) { 20 }
                default { 10 }
            }
            [pscustomobject]@{ Address = $text; Adapter = $adapter.Name; Score = $typeScore + $(if ($hasGateway) { 100 } else { 0 }) }
        }
    }
    return $candidates | Sort-Object Score -Descending | Select-Object -First 1
}
