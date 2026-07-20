param(
    [string]$PythonCommand = "python",
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$Python = Get-Command $PythonCommand -ErrorAction SilentlyContinue
if (-not $Python) {
    throw "Python was not found. Install Python 3.12, then reopen PowerShell."
}

& $Python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Python 3.12 is recommended."
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python.Source -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment."
    }
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements-server.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install project dependencies."
}

foreach ($Directory in @("data", "logs", "reports")) {
    New-Item -ItemType Directory -Path (Join-Path $ProjectRoot $Directory) -Force | Out-Null
}

$env:APP_ENV = "local"
$env:AUTH_ENABLED = "false"
$env:PYTHONUTF8 = "1"

& $VenvPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    throw "Automated tests failed. The application was not started."
}

Write-Host "Local installation completed."
if ($Start) {
    & (Join-Path $PSScriptRoot "start_local_8501.ps1")
} else {
    Write-Host "Start later with: powershell -ExecutionPolicy Bypass -File scripts\start_local_8501.ps1"
}
