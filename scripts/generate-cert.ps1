param(
    [string]$CertDir = "certs"
)

$ErrorActionPreference = "Stop"

function Join-ByteArrays {
    param([object[]]$Parts)
    $stream = [System.IO.MemoryStream]::new()
    try {
        foreach ($part in $Parts) {
            $bytes = [byte[]]$part
            $stream.Write($bytes, 0, $bytes.Length)
        }
        return $stream.ToArray()
    } finally {
        $stream.Dispose()
    }
}

function ConvertTo-DerLength {
    param([int]$Length)
    if ($Length -lt 128) { return [byte[]]@([byte]$Length) }
    $octets = [System.Collections.Generic.List[byte]]::new()
    while ($Length -gt 0) {
        $octets.Insert(0, [byte]($Length -band 0xff))
        $Length = $Length -shr 8
    }
    return [byte[]](@([byte](0x80 -bor $octets.Count)) + $octets.ToArray())
}

function ConvertTo-DerInteger {
    param([byte[]]$Value)
    $offset = 0
    while ($offset -lt ($Value.Length - 1) -and $Value[$offset] -eq 0) { $offset++ }
    $content = [byte[]]$Value[$offset..($Value.Length - 1)]
    if (($content[0] -band 0x80) -ne 0) { $content = [byte[]](@(0) + $content) }
    return Join-ByteArrays @([byte[]]@(0x02), (ConvertTo-DerLength $content.Length), $content)
}

function ConvertTo-Pkcs1PrivateKey {
    param([System.Security.Cryptography.RSAParameters]$Parameters)
    $body = Join-ByteArrays @(
        (ConvertTo-DerInteger ([byte[]]@(0))),
        (ConvertTo-DerInteger $Parameters.Modulus),
        (ConvertTo-DerInteger $Parameters.Exponent),
        (ConvertTo-DerInteger $Parameters.D),
        (ConvertTo-DerInteger $Parameters.P),
        (ConvertTo-DerInteger $Parameters.Q),
        (ConvertTo-DerInteger $Parameters.DP),
        (ConvertTo-DerInteger $Parameters.DQ),
        (ConvertTo-DerInteger $Parameters.InverseQ)
    )
    return Join-ByteArrays @([byte[]]@(0x30), (ConvertTo-DerLength $body.Length), $body)
}

function ConvertTo-Pem {
    param([string]$Label, [byte[]]$Bytes)
    $base64 = [Convert]::ToBase64String($Bytes)
    $lines = for ($index = 0; $index -lt $base64.Length; $index += 64) {
        $base64.Substring($index, [Math]::Min(64, $base64.Length - $index))
    }
    return "-----BEGIN $Label-----`n$($lines -join "`n")`n-----END $Label-----`n"
}

New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
$keyPath = Join-Path $CertDir "key.pem"
$certPath = Join-Path $CertDir "cert.pem"

$ips = @(
    foreach ($adapter in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
        if ($adapter.NetworkInterfaceType -in @(
            [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback,
            [System.Net.NetworkInformation.NetworkInterfaceType]::Tunnel
        )) { continue }
        foreach ($entry in $adapter.GetIPProperties().UnicastAddresses) {
            $address = $entry.Address
            if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { continue }
            if ([System.Net.IPAddress]::IsLoopback($address)) { continue }
            $text = $address.IPAddressToString
            if ($text -notlike "169.254.*") { $text }
        }
    }
) | Select-Object -Unique

$rsa = [System.Security.Cryptography.RSA]::Create(2048)
try {
    $request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        "CN=iphone-camera-bridge.local",
        $rsa,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
    $san = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $san.AddDnsName("localhost")
    $san.AddDnsName("iphone-camera-bridge.local")
    $san.AddIpAddress([System.Net.IPAddress]::Parse("127.0.0.1"))
    foreach ($ip in $ips) { $san.AddIpAddress([System.Net.IPAddress]::Parse($ip)) }
    $request.CertificateExtensions.Add($san.Build())
    $request.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true)
    )
    $request.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
            [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature,
            $true
        )
    )
    $certificate = $request.CreateSelfSigned([DateTimeOffset]::Now.AddDays(-1), [DateTimeOffset]::Now.AddYears(10))
    try {
        $keyDer = ConvertTo-Pkcs1PrivateKey ($rsa.ExportParameters($true))
        $certDer = $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($keyPath, (ConvertTo-Pem "RSA PRIVATE KEY" $keyDer), $utf8)
        [System.IO.File]::WriteAllText($certPath, (ConvertTo-Pem "CERTIFICATE" $certDer), $utf8)
    } finally {
        $certificate.Dispose()
    }
} finally {
    $rsa.Dispose()
}

Write-Host "[camera-bridge] Generated local HTTPS certificate without OpenSSL:"
Write-Host "  $certPath"
