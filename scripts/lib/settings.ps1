function Read-IntegerSetting {
    param([string]$Label, [int]$Current, [int]$Minimum, [int]$Maximum)
    while ($true) {
        $text = Read-Host "$Label [$Minimum-$Maximum] (default $Current)"
        if ([string]::IsNullOrWhiteSpace($text)) { return $Current }
        $value = 0
        if ([int]::TryParse($text, [ref]$value) -and $value -ge $Minimum -and $value -le $Maximum) { return $value }
        Write-Warning "Enter an integer from $Minimum to $Maximum."
    }
}

function Read-BooleanSetting {
    param([string]$Label, [bool]$Current)
    $defaultText = if ($Current) { "y" } else { "n" }
    while ($true) {
        $text = Read-Host "$Label [y/n] (default $defaultText)"
        if ([string]::IsNullOrWhiteSpace($text)) { return $Current }
        if ($text -match "^(y|yes)$") { return $true }
        if ($text -match "^(n|no)$") { return $false }
        Write-Warning "Enter y or n."
    }
}

function Get-AiProviderSettings {
    param([string]$ConfigFile)
    $config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $active = [string]$config.active_provider
    $provider = $config.providers.$active
    if (-not $provider) { throw "active_provider '$active' is not defined" }
    return [pscustomobject]@{
        Config = $config
        Name = $active
        Label = [string]$provider.label
        ApiKeyEnv = [string]$provider.api_key_env
        Model = [string]$provider.model
        Available = @($config.providers.PSObject.Properties.Name)
    }
}

function Set-ActiveAiProvider {
    param([string]$ConfigFile, [string]$ProviderName)
    $settings = Get-AiProviderSettings -ConfigFile $ConfigFile
    if ($ProviderName -notin $settings.Available) { throw "Unknown AI provider: $ProviderName" }
    $settings.Config.active_provider = $ProviderName
    $settings.Config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ConfigFile -Encoding UTF8
    return Get-AiProviderSettings -ConfigFile $ConfigFile
}

function Read-SecretText {
    param([string]$Prompt)
    $secureValue = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
