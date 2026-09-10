param(
    [Parameter(Mandatory = $true)]
    [string]$EnvId,
    [Parameter(Mandatory = $true)]
    [string]$SourceDb,
    [string]$CloudPath = "miniapp-feed/stock_recommendations.seed.gz",
    [Parameter(Mandatory = $true)]
    [string]$PublicBaseUrl
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunId = [guid]::NewGuid().ToString("N")
$TempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
$Seed = Join-Path $env:TEMP "stock-quant-feed-$RunId.seed.gz"
$Downloaded = Join-Path $env:TEMP "stock-quant-feed-$RunId.verify.gz"

function Assert-SafeTempPath([string]$Path) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    if (-not $FullPath.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the temporary directory: $FullPath"
    }
}

function Get-Sha256([string]$Path) {
    $Stream = [IO.File]::OpenRead($Path)
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = $Hasher.ComputeHash($Stream)
        return ([BitConverter]::ToString($Bytes)).Replace("-", "")
    }
    finally {
        $Hasher.Dispose()
        $Stream.Dispose()
    }
}

try {
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python)) {
        $Python = (Get-Command python.exe -ErrorAction Stop).Source
    }
    & $Python (Join-Path $PSScriptRoot "build_cloudbase_seed.py") `
        --source $SourceDb `
        --output $Seed
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to build the sanitized mini program feed."
    }

    & npx.cmd --yes --package "@cloudbase/cli" tcb hosting deploy `
        $Seed $CloudPath -e $EnvId --json | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "CloudBase static storage upload failed."
    }

    $ExpectedHash = Get-Sha256 $Seed
    $Url = $PublicBaseUrl.TrimEnd("/") + "/" + $CloudPath.TrimStart("/") + `
        "?verify=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Downloaded -TimeoutSec 40
    $ActualHash = Get-Sha256 $Downloaded
    if ($ExpectedHash -ne $ActualHash) {
        throw "CloudBase feed verification failed."
    }
    Write-Output "Mini program feed published and verified."
}
finally {
    foreach ($Path in @($Seed, $Downloaded)) {
        if (Test-Path -LiteralPath $Path) {
            Assert-SafeTempPath $Path
            Remove-Item -LiteralPath $Path -Force
        }
    }
}
