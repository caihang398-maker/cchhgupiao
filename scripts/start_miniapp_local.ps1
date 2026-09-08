param(
    [int]$Port = 8512
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python was not found. Run scripts\setup_local.ps1 first."
}

$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:APP_ENV = "local"
$env:AUTH_ENABLED = "false"
$env:MINIAPP_ALLOW_LOCAL_DEV = "true"

Write-Host "Miniapp API: http://127.0.0.1:$Port/api/v1"
Write-Host "Keep this window open while using WeChat Developer Tools."
& $Python -m stock_quant.miniapp_api --host 127.0.0.1 --port $Port
