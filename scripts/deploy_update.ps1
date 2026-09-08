param(
    [string]$TargetRoot = "E:\stock-quant",
    [string]$TaskName = "StockQuantWeb",
    [string]$PaymentTaskName = "StockQuantPaymentWebhook",
    [int]$WebPort = 8501,
    [int]$PaymentWebhookPort = 8511
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $PSScriptRoot
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupRoot = "E:\stock-quant-backups\$Timestamp"

function Stop-WebListener {
    param([int]$Port)
    $ProcessIds = Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ProcessId in $ProcessIds) {
        if ($ProcessId) {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-WebService {
    param(
        [object]$ScheduledTask,
        [string]$Name,
        [string]$Root,
        [int]$Port
    )
    if ($ScheduledTask) {
        Start-ScheduledTask -TaskName $Name
        return
    }
    $StartScript = Join-Path $Root "scripts\start_server.ps1"
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -Port $Port -Address `"0.0.0.0`"" `
        -WorkingDirectory $Root `
        -WindowStyle Hidden
}

$ManifestPath = Join-Path $PackageRoot "release_manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath)) {
    throw "Release manifest is missing. Rebuild the package with scripts\build_release.ps1."
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$Manifest.format_version -ne 1) {
    throw "Unsupported release manifest format: $($Manifest.format_version)"
}
$FileEntries = @($Manifest.files)
if ($FileEntries.Count -eq 0 -or [int]$Manifest.file_count -ne $FileEntries.Count) {
    throw "Release manifest file count is invalid."
}
$Files = @()
foreach ($Entry in $FileEntries) {
    $RelativePath = ([string]$Entry.path).Replace("/", "\")
    if (
        [string]::IsNullOrWhiteSpace($RelativePath) `
        -or [System.IO.Path]::IsPathRooted($RelativePath) `
        -or $RelativePath.Split("\") -contains ".."
    ) {
        throw "Unsafe path in release manifest: $RelativePath"
    }
    $SourceFile = Join-Path $PackageRoot $RelativePath
    if (-not (Test-Path -LiteralPath $SourceFile -PathType Leaf)) {
        throw "Package file is missing: $RelativePath"
    }
    $ActualHash = (Get-FileHash -LiteralPath $SourceFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -ne ([string]$Entry.sha256).ToLowerInvariant()) {
        throw "Package file hash mismatch: $RelativePath"
    }
    $Files += $RelativePath
}
$Files += "release_manifest.json"

if (-not (Test-Path -LiteralPath (Join-Path $TargetRoot "app.py"))) {
    throw "Target project was not found: $TargetRoot"
}

foreach ($RelativePath in $Files) {
    if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $RelativePath))) {
        throw "Package file is missing: $RelativePath"
    }
}

New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

foreach ($RelativePath in $Files) {
    $ExistingFile = Join-Path $TargetRoot $RelativePath
    if (Test-Path -LiteralPath $ExistingFile) {
        $BackupFile = Join-Path $BackupRoot $RelativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupFile) -Force | Out-Null
        Copy-Item -LiteralPath $ExistingFile -Destination $BackupFile -Force
    }
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$PaymentTask = Get-ScheduledTask -TaskName $PaymentTaskName -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
}
if ($PaymentTask) {
    Stop-ScheduledTask -TaskName $PaymentTaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Stop-WebListener -Port $PaymentWebhookPort
}

Stop-WebListener -Port $WebPort

try {
    foreach ($RelativePath in $Files) {
        $SourceFile = Join-Path $PackageRoot $RelativePath
        $TargetFile = Join-Path $TargetRoot $RelativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $TargetFile) -Force | Out-Null
        Copy-Item -LiteralPath $SourceFile -Destination $TargetFile -Force
    }

    foreach ($Entry in $FileEntries) {
        $RelativePath = ([string]$Entry.path).Replace("/", "\")
        $TargetFile = Join-Path $TargetRoot $RelativePath
        $TargetHash = (Get-FileHash -LiteralPath $TargetFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($TargetHash -ne ([string]$Entry.sha256).ToLowerInvariant()) {
            throw "Target file hash mismatch after copy: $RelativePath"
        }
    }

    $Python = Join-Path $TargetRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Virtual environment Python was not found: $Python"
    }

    Push-Location -LiteralPath $TargetRoot
    try {
        $env:APP_ENV = "production"
        $PythonFiles = @(
            $Files |
            Where-Object { $_.EndsWith(".py", [System.StringComparison]::OrdinalIgnoreCase) } |
            ForEach-Object { Join-Path $TargetRoot $_ }
        )
        & $Python -m py_compile @PythonFiles
        if ($LASTEXITCODE -ne 0) {
            throw "Python syntax check failed."
        }

        & $Python -c "from stock_quant.storage import load_limit_up_ladder, load_sentiment_history, save_sentiment_snapshot; from stock_quant.sentiment import fetch_sentiment_bundle; print('Sentiment imports: ok')"
        if ($LASTEXITCODE -ne 0) {
            throw "Sentiment module import check failed."
        }

        & $Python scripts\release_check.py
        if ($LASTEXITCODE -ne 0) {
            throw "Release check failed."
        }
    } finally {
        Pop-Location
    }

    Start-WebService -ScheduledTask $Task -Name $TaskName -Root $TargetRoot -Port $WebPort
    if ($PaymentTask) {
        Start-ScheduledTask -TaskName $PaymentTaskName
    }

    $Health = $null
    $LastHealthError = $null
    for ($Attempt = 1; $Attempt -le 45; $Attempt++) {
        try {
            $Health = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri "http://127.0.0.1:$WebPort/_stcore/health" `
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
        $TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        $RecentLog = ""
        $LogPath = Join-Path $TargetRoot "logs\server.log"
        if (Test-Path -LiteralPath $LogPath) {
            $RecentLog = (Get-Content -LiteralPath $LogPath -Tail 80) -join "`n"
        }
        throw "Health check failed after waiting. Last error: $LastHealthError`nTask info: $TaskInfo`nRecent server log:`n$RecentLog"
    }

    $PaymentHealth = $null
    $LastPaymentHealthError = $null
    if ($PaymentTask) {
        for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
            try {
                $PaymentHealth = Invoke-WebRequest `
                    -UseBasicParsing `
                    -Uri "http://127.0.0.1:$PaymentWebhookPort/health" `
                    -TimeoutSec 5
                if ($PaymentHealth.StatusCode -eq 200) {
                    break
                }
            } catch {
                $LastPaymentHealthError = $_
            }
            Start-Sleep -Seconds 2
        }
        if (-not $PaymentHealth -or $PaymentHealth.StatusCode -ne 200) {
            $PaymentTaskInfo = Get-ScheduledTaskInfo `
                -TaskName $PaymentTaskName `
                -ErrorAction SilentlyContinue
            $PaymentLog = ""
            $PaymentLogPath = Join-Path $TargetRoot "logs\payment-webhook.log"
            if (Test-Path -LiteralPath $PaymentLogPath) {
                $PaymentLog = (Get-Content -LiteralPath $PaymentLogPath -Tail 80) -join "`n"
            }
            throw "Payment webhook health check failed. Last error: $LastPaymentHealthError`nTask info: $PaymentTaskInfo`nRecent payment log:`n$PaymentLog"
        }
    }

    Write-Host "Update completed."
    Write-Host "Backup: $BackupRoot"
    Write-Host "Health: $($Health.StatusCode) $($Health.Content.Trim())"
    if ($PaymentHealth) {
        Write-Host "Payment health: $($PaymentHealth.StatusCode) $($PaymentHealth.Content.Trim())"
    }
} catch {
    Write-Warning "Update failed. Restoring the backup."

    if ($PaymentTask) {
        Stop-ScheduledTask -TaskName $PaymentTaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Stop-WebListener -Port $PaymentWebhookPort
    }
    Stop-WebListener -Port $WebPort

    foreach ($RelativePath in $Files) {
        $BackupFile = Join-Path $BackupRoot $RelativePath
        if (Test-Path -LiteralPath $BackupFile) {
            $TargetFile = Join-Path $TargetRoot $RelativePath
            New-Item -ItemType Directory -Path (Split-Path -Parent $TargetFile) -Force | Out-Null
            Copy-Item -LiteralPath $BackupFile -Destination $TargetFile -Force
        }
    }

    try {
        Start-WebService -ScheduledTask $Task -Name $TaskName -Root $TargetRoot -Port $WebPort
        if ($PaymentTask) {
            Start-ScheduledTask -TaskName $PaymentTaskName
        }
    } catch {
        Write-Warning "Backup was restored, but the previous service could not be restarted: $_"
    }

    throw
}
