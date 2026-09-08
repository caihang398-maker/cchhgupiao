param(
    [string]$TaskName = "StockQuantPaymentWebhook",
    [int]$Port = 8511
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    Get-ScheduledTaskInfo |
    Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime

$Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Connection) {
    $Connection | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
} else {
    Write-Warning "No process is listening on payment webhook port $Port."
}

try {
    $Health = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "http://127.0.0.1:$Port/health" `
        -TimeoutSec 5
    Write-Host "Payment health: $($Health.StatusCode)"
} catch {
    Write-Warning "Payment webhook health check failed: $($_.Exception.Message)"
}

$LogFile = Join-Path $ProjectRoot "logs\payment-webhook.log"
if (Test-Path -LiteralPath $LogFile) {
    Write-Host ""
    Write-Host "Recent payment webhook log:"
    Get-Content -LiteralPath $LogFile -Tail 50
}
