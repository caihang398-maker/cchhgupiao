param(
    [string]$EnvId = "a125378155-d6gsz6qfn63b5b12b",
    [string]$ServiceName = "gupiaoxiaochengxu",
    [ValidateSet("research", "personal_records")]
    [string]$ProductMode = "research",
    [string]$FeedUrl = "https://a125378155-d6gsz6qfn63b5b12b-1483000192.tcloudbaseapp.com/miniapp-feed/stock_recommendations.seed.gz",
    [int]$WebPort = 80
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
$runId = [guid]::NewGuid().ToString("N")
$archive = Join-Path $env:TEMP "stock-quant-cloudbase-$runId.zip"
$stage = Join-Path $env:TEMP "stock-quant-cloudbase-$runId"

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Message (exit code $LASTEXITCODE)"
    }
}

function Invoke-TcbJson([string[]]$Arguments) {
    $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
    $bridgePath = Join-Path $PSScriptRoot "tcb_cli_bridge.js"
    if (-not (Test-Path -LiteralPath $bridgePath)) {
        throw "Unable to locate the CloudBase CLI bridge: $bridgePath"
    }
    try {
        $env:TCB_CLI_ARGUMENTS = ConvertTo-Json @($Arguments) -Compress
        $raw = (& $nodePath $bridgePath 2>&1) -join "`n"
    }
    finally {
        Remove-Item Env:TCB_CLI_ARGUMENTS -ErrorAction SilentlyContinue
    }
    if ($LASTEXITCODE -ne 0) {
        throw "CloudBase CLI command failed (exit code $LASTEXITCODE):`n$raw"
    }
    $jsonStart = $raw.IndexOf("{")
    if ($jsonStart -lt 0) {
        throw "CloudBase CLI did not return JSON: $raw"
    }
    return $raw.Substring($jsonStart) | ConvertFrom-Json
}

function Get-ServiceDetail {
    $body = @{
        EnvId = $EnvId
        ServerName = $ServiceName
    } | ConvertTo-Json -Compress
    return Invoke-TcbJson @(
        "api", "tcbr", "DescribeCloudRunServerDetail",
        "--api-version", "2022-02-17",
        "--body", $body,
        "--json"
    )
}

function Assert-SafeTempPath([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the temporary directory: $fullPath"
    }
}

try {
    Write-Host "[1/6] Creating a minimal deployment package from the current Git commit..."
    & git -C $projectRoot archive --format=zip --output=$archive HEAD `
        Dockerfile requirements-miniapp.txt stock_quant deploy/cloudbase
    Assert-LastExitCode "Unable to create the deployment archive"
    Expand-Archive -LiteralPath $archive -DestinationPath $stage -Force

    Write-Host "[2/6] Preserving secrets and applying the low-cost runtime configuration..."
    $detail = (Get-ServiceDetail).data
    $current = $detail.ServerConfig
    $runtimeEnv = [ordered]@{}
    if (-not [string]::IsNullOrWhiteSpace([string]$current.EnvParams)) {
        $existingEnv = $current.EnvParams | ConvertFrom-Json
        foreach ($property in $existingEnv.PSObject.Properties) {
            $runtimeEnv[$property.Name] = [string]$property.Value
        }
    }
    if (-not $runtimeEnv.Contains("MINIAPP_TOKEN_SECRET") -or
        ([string]$runtimeEnv["MINIAPP_TOKEN_SECRET"]).Length -lt 32) {
        $runtimeEnv["MINIAPP_TOKEN_SECRET"] = `
            [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    }
    $runtimeEnv["APP_ENV"] = "production"
    $runtimeEnv["AUTH_ENABLED"] = "false"
    $runtimeEnv["WECHAT_MINIAPP_APP_ID"] = "wx7ded5b1d303e1b1d"
    $runtimeEnv["MINIAPP_TRUST_PROXY"] = "true"
    $runtimeEnv["MINIAPP_TRUST_CLOUDBASE_IDENTITY"] = "true"
    $runtimeEnv["MINIAPP_CLOUDBASE_PERSONAL_MODE"] = "true"
    $runtimeEnv["MINIAPP_PRODUCT_MODE"] = $ProductMode
    $runtimeEnv["MINIAPP_CLOUD_SQLITE_SYNC"] = if ($ProductMode -eq "personal_records") { "false" } else { "true" }
    $runtimeEnv["MINIAPP_CLOUD_FEED_URL"] = if ($ProductMode -eq "personal_records") { "" } else { $FeedUrl }
    $runtimeEnv["MINIAPP_CLOUD_FEED_REFRESH_SECONDS"] = "30"
    $runtimeEnv["MINIAPP_REQUIRE_PERSISTENT_STORAGE"] = "false"
    if ($ProductMode -eq "personal_records") {
        $runtimeEnv["MINIAPP_PAYMENT_LIVE_ENABLED"] = "false"
    }

    $serverConfig = [ordered]@{
        EnvId = $EnvId
        ServerName = $ServiceName
        OpenAccessTypes = @($current.OpenAccessTypes)
        Cpu = 0.25
        Mem = 0.5
        MinNum = 0
        MaxNum = 1
        PolicyDetails = @($current.PolicyDetails)
        CustomLogs = $current.CustomLogs
        EnvParams = ($runtimeEnv | ConvertTo-Json -Compress)
        InitialDelaySeconds = 2
        CreateTime = $current.CreateTime
        Port = $WebPort
        HasDockerfile = $true
        Dockerfile = "Dockerfile"
        BuildDir = "."
    }
    $configBody = @{
        EnvId = $EnvId
        ServerBaseConfig = $serverConfig
    } | ConvertTo-Json -Depth 10 -Compress
    $null = Invoke-TcbJson @(
        "api", "tcbr", "UpdateCloudRunServerConfig",
        "--api-version", "2022-02-17",
        "--body", $configBody,
        "--json"
    )

    Write-Host "[3/6] Uploading and deploying the container..."
    "" | & npx.cmd --yes --package "@cloudbase/cli" tcb cloudrun deploy `
        -e $EnvId -s $ServiceName --port $WebPort --source $stage --force --wait --json
    Assert-LastExitCode "CloudBase deployment failed"

    Write-Host "[4/6] Verifying the public health endpoint before it is closed..."
    $detail = (Get-ServiceDetail).data
    $healthUrl = ([string]$detail.BaseInfo.DefaultDomainName).TrimEnd("/") + "/health"
    $health = $null
    foreach ($attempt in 1..12) {
        try {
            $health = Invoke-RestMethod -UseBasicParsing -Uri $healthUrl -TimeoutSec 20
            if ($health.ok -and $health.data.status -eq "ok") {
                break
            }
        }
        catch {
            if ($attempt -eq 12) { throw }
        }
        Start-Sleep -Seconds 5
    }
    if (-not $health.ok) {
        throw "CloudBase health check failed"
    }

    Write-Host "[5/6] Restricting inbound access to WeChat Mini Program calls only..."
    $accessBody = [ordered]@{
        EnvId = $EnvId
        ServerName = $ServiceName
        Items = @(
            [ordered]@{
                Key = "AccessTypes"
                ArrayValue = @("MINIAPP")
            }
        )
    } | ConvertTo-Json -Depth 6 -Compress
    $null = Invoke-TcbJson @(
        "api", "tcbr", "SubmitServerConfigChangeDiff",
        "--api-version", "2022-02-17",
        "--body", $accessBody,
        "--json"
    )

    Write-Host "[6/6] Checking the final service state..."
    $final = (Get-ServiceDetail).data
    $accessTypes = @($final.ServerConfig.OpenAccessTypes)
    if ($accessTypes.Count -ne 1 -or $accessTypes[0] -ne "MINIAPP") {
        throw "Public access is still enabled: $($accessTypes -join ', ')"
    }
    [pscustomobject]@{
        Status = $final.BaseInfo.Status
        Version = $final.OnlineVersionInfos[0].VersionName
        Cpu = $final.ServerConfig.Cpu
        MemoryGB = $final.ServerConfig.Mem
        MinInstances = $final.ServerConfig.MinNum
        MaxInstances = $final.ServerConfig.MaxNum
        AccessTypes = $accessTypes -join ","
    } | Format-List
}
finally {
    foreach ($path in @($archive, $stage)) {
        if (Test-Path -LiteralPath $path) {
            Assert-SafeTempPath $path
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}
