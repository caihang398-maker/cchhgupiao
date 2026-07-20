#requires -RunAsAdministrator

param(
    [int]$WebPort = 8501,
    [string]$BindAddress = "127.0.0.1",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 3306,
    [string]$DbName = "stock_quant_saas",
    [string]$DbUser = "stock_quant_app",
    [string]$TaskName = "StockQuantWeb",
    [switch]$OpenFirewall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

function Set-AppEnvironment([string]$Name, [string]$Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value, "Machine")
    Set-Item -Path "Env:$Name" -Value $Value
}

Write-Host "Project: $ProjectRoot"
Write-Host "Checking Python 3.12..."

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    & $PyLauncher.Source -3.12 -c "import sys; print(sys.version)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 is not installed. Install 64-bit Python 3.12 first."
    }
    $CreateVenv = { & $PyLauncher.Source -3.12 -m venv ".venv" }
} else {
    $SystemPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $SystemPython) {
        throw "Python is not installed. Install 64-bit Python 3.12 first."
    }
    & $SystemPython.Source -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 is required."
    }
    $CreateVenv = { & $SystemPython.Source -m venv ".venv" }
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $CreateVenv
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements-server.txt
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed."
}

$SecurePassword = Read-Host "MySQL application password for '$DbUser'" -AsSecureString
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
try {
    $DbPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($PasswordPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer)
}
if ([string]::IsNullOrWhiteSpace($DbPassword)) {
    throw "MySQL password cannot be empty."
}

Set-AppEnvironment "AUTH_ENABLED" "true"
Set-AppEnvironment "APP_ENV" "production"
Set-AppEnvironment "DB_HOST" $DbHost
Set-AppEnvironment "DB_PORT" ([string]$DbPort)
Set-AppEnvironment "DB_NAME" $DbName
Set-AppEnvironment "DB_USER" $DbUser
Set-AppEnvironment "DB_PASSWORD" $DbPassword
Set-AppEnvironment "PYTHONUTF8" "1"
Set-AppEnvironment "PYTHONUNBUFFERED" "1"
$DbPassword = $null

New-Item -ItemType Directory -Force -Path "data", "data\cache", "reports", "logs" | Out-Null

& $Python scripts/release_check.py
if ($LASTEXITCODE -ne 0) {
    throw "Release check failed. Verify the MySQL schema, account and password."
}

$StartScript = Join-Path $ProjectRoot "scripts\start_server.ps1"
$TaskArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -Port $WebPort -Address `"$BindAddress`""
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $TaskArguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "A-share quantitative recommendation web service" `
    -Force | Out-Null

if ($OpenFirewall) {
    $RuleName = "StockQuant Web $WebPort"
    if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule `
            -DisplayName $RuleName `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $WebPort `
            -Action Allow | Out-Null
    }
}

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8

$HealthUrl = "http://127.0.0.1:$WebPort/_stcore/health"
$Health = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 10
if ($Health.StatusCode -ne 200 -or $Health.Content.Trim() -ne "ok") {
    throw "Web service did not pass health check: $HealthUrl"
}

Write-Host ""
Write-Host "Installation completed."
Write-Host "Task: $TaskName"
Write-Host "Health: $HealthUrl"
Write-Host "Log: $(Join-Path $ProjectRoot 'logs\server.log')"
