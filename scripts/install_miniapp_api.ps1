#requires -RunAsAdministrator

param(
    [string]$TaskName = "StockQuantMiniappApi",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8512
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python was not found: $Python"
}

Push-Location -LiteralPath $ProjectRoot
try {
    & $Python -c "from stock_quant.miniapp_api import validate_startup; validate_startup('$HostAddress'); print('Miniapp API configuration: ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "Miniapp API configuration check failed."
    }
} finally {
    Pop-Location
}

$StartScript = Join-Path $ProjectRoot "scripts\start_miniapp_api.ps1"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -HostAddress `"$HostAddress`" -Port $Port"
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $Arguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "A-share quantitative WeChat Mini Program API" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
$Health = $null
$LastHealthError = $null
for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
    try {
        $Health = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$Port/health" `
            -TimeoutSec 5
        if ($Health.StatusCode -eq 200) {
            break
        }
    } catch {
        $LastHealthError = $_
    }
    Start-Sleep -Seconds 2
}
if (-not $Health -or $Health.StatusCode -ne 200) {
    $LogFile = Join-Path $ProjectRoot "logs\miniapp-api.log"
    $RecentLog = ""
    if (Test-Path -LiteralPath $LogFile) {
        $RecentLog = (Get-Content -LiteralPath $LogFile -Tail 80) -join "`n"
    }
    throw "Miniapp API health check failed. Last error: $LastHealthError`nRecent log:`n$RecentLog"
}

Write-Host "Miniapp API task installed: $TaskName"
Write-Host "Local health: http://127.0.0.1:$Port/health"
Write-Host "Reverse proxy target: http://127.0.0.1:$Port/api/v1"
