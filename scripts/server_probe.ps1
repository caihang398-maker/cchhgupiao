$ErrorActionPreference = "Continue"

Write-Host "=== 系统信息 ==="
Get-CimInstance Win32_OperatingSystem |
    Select-Object Caption, Version, OSArchitecture |
    Format-List

Write-Host "=== 磁盘空间 ==="
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    Select-Object DeviceID,
        @{Name = "总容量GB"; Expression = { [math]::Round($_.Size / 1GB, 2) }},
        @{Name = "可用容量GB"; Expression = { [math]::Round($_.FreeSpace / 1GB, 2) }} |
    Format-Table -AutoSize

Write-Host "=== Python ==="
$PythonCommands = @("py", "python")
foreach ($Command in $PythonCommands) {
    $Resolved = Get-Command $Command -ErrorAction SilentlyContinue
    if ($Resolved) {
        Write-Host "$Command : $($Resolved.Source)"
        & $Resolved.Source --version
    } else {
        Write-Host "$Command : 未安装或不在PATH"
    }
}

Write-Host "=== MySQL服务 ==="
Get-CimInstance Win32_Service |
    Where-Object {
        $_.Name -match "mysql|maria" -or
        $_.DisplayName -match "mysql|maria"
    } |
    Select-Object Name, DisplayName, State, StartMode, PathName |
    Format-List

Write-Host "=== MySQL客户端 ==="
$Mysql = Get-Command mysql -ErrorAction SilentlyContinue
if ($Mysql) {
    Write-Host "mysql : $($Mysql.Source)"
    & $Mysql.Source --version
} else {
    Write-Host "mysql : 不在PATH，可从上方MySQL服务路径判断安装目录"
}

Write-Host "=== 端口监听 ==="
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(80, 443, 3306, 8501, 50088) } |
    Select-Object LocalAddress, LocalPort, OwningProcess |
    Sort-Object LocalPort |
    Format-Table -AutoSize

Write-Host "=== 项目文件 ==="
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Write-Host "项目目录：$ProjectRoot"
foreach ($RelativePath in @(
    "app.py",
    "requirements-server.txt",
    "database\mysql57_schema.sql",
    "scripts\install_windows_server.ps1"
)) {
    $FullPath = Join-Path $ProjectRoot $RelativePath
    Write-Host "$RelativePath : $(Test-Path -LiteralPath $FullPath)"
}
