$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "logs") -Force | Out-Null
$env:AUTH_ENABLED = "false"
$env:APP_ENV = "local"
$env:PYTHONUTF8 = "1"

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $Python -and (Test-Path -LiteralPath "C:\Program Files\Python312\python.exe")) {
    $Python = "C:\Program Files\Python312\python.exe"
}
if (-not $Python) {
    throw "Python was not found."
}

$Existing = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($ProcessId in $Existing) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$Arguments = @(
    "-m", "streamlit", "run", "app.py",
    "--server.port", "8501",
    "--server.address", "127.0.0.1",
    "--server.headless", "true"
)
Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput (Join-Path $ProjectRoot "logs\local-8501.out.log") `
    -RedirectStandardError (Join-Path $ProjectRoot "logs\local-8501.err.log") `
    -WindowStyle Hidden

$Health = $null
for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
    Start-Sleep -Seconds 1
    try {
        $Health = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:8501/_stcore/health" `
            -TimeoutSec 3
        if ($Health.StatusCode -eq 200) {
            break
        }
    } catch {
        $Health = $null
    }
}
if (-not $Health -or $Health.StatusCode -ne 200) {
    throw "Port 8501 failed its health check."
}
Write-Host "Local app is ready: http://127.0.0.1:8501"
