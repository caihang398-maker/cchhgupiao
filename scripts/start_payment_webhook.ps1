param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8511
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:APP_ENV = "production"

$PaymentEnabled = $env:PAYMENT_ENABLED -and $env:PAYMENT_ENABLED.Trim().ToLowerInvariant() -in @(
    "1", "true", "yes", "on"
)
if (-not $PaymentEnabled) {
    throw "PAYMENT_ENABLED is not enabled."
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python was not found: $Python"
}

$LogDirectory = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogFile = Join-Path $LogDirectory "payment-webhook.log"

& $Python -m stock_quant.payment_webhook `
    --host $HostAddress `
    --port $Port 2>&1 |
    Tee-Object -FilePath $LogFile -Append
