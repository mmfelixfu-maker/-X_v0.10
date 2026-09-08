param(
    [Parameter(Mandatory = $true)][string]$ProjectDirectory
)

$runtimeRoot = Join-Path $ProjectDirectory "runtime"
$runtimeDirectories = @(
    (Join-Path $runtimeRoot "bin"),
    (Join-Path $runtimeRoot "node"),
    (Join-Path $runtimeRoot "bun"),
    (Join-Path $runtimeRoot "python")
) | Where-Object { Test-Path -LiteralPath $_ }

if ($runtimeDirectories.Count -gt 0) {
    $env:PATH = (($runtimeDirectories -join ";") + ";" + $env:PATH)
}
