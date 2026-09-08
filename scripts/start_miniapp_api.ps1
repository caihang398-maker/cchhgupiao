param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8512
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:APP_ENV = "production"

$AuthEnabled = $env:AUTH_ENABLED -and $env:AUTH_ENABLED.Trim().ToLowerInvariant() -in @(
    "1", "true", "yes", "on"
)
if (-not $AuthEnabled) {
    throw "Production miniapp API requires AUTH_ENABLED=true."
}
if ([string]::IsNullOrWhiteSpace($env:MINIAPP_TOKEN_SECRET) -or $env:MINIAPP_TOKEN_SECRET.Length -lt 32) {
    throw "MINIAPP_TOKEN_SECRET must contain at least 32 characters."
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python was not found: $Python"
}

$LogDirectory = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogFile = Join-Path $LogDirectory "miniapp-api.log"

& $Python -m stock_quant.miniapp_api `
    --host $HostAddress `
    --port $Port 2>&1 |
    Tee-Object -FilePath $LogFile -Append
