param(
    [int]$WebPort = 8501,
    [string]$TaskName = "StockQuantWeb"
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    Get-ScheduledTaskInfo |
    Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime

$Connection = Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue
if ($Connection) {
    $Connection | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
} else {
    Write-Warning "No process is listening on port $WebPort."
}

try {
    $Health = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "http://127.0.0.1:$WebPort/_stcore/health" `
        -TimeoutSec 5
    Write-Host "Health: $($Health.StatusCode) $($Health.Content.Trim())"
} catch {
    Write-Warning "Health check failed: $($_.Exception.Message)"
}

$LogFile = Join-Path $ProjectRoot "logs\server.log"
if (Test-Path -LiteralPath $LogFile) {
    Write-Host ""
    Write-Host "Recent server log:"
    Get-Content -LiteralPath $LogFile -Tail 50
}
