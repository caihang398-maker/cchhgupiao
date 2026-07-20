param(
    [int]$Port = 8501,
    [string]$Address = "127.0.0.1"
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
    throw "Production startup requires AUTH_ENABLED=true. Use start_local_8501.ps1 for local testing."
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

$LogDirectory = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogFile = Join-Path $LogDirectory "server.log"

& $Python scripts/release_check.py 2>&1 | Tee-Object -FilePath $LogFile -Append
if ($LASTEXITCODE -ne 0) {
    throw "Release check failed. The web service was not started."
}

& $Python -m streamlit run app.py `
    --server.port $Port `
    --server.address $Address `
    --server.headless true
