param(
    [switch]$SkipTests,
    [switch]$FullAppCheck
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command python.exe -ErrorAction Stop).Source
}

$Arguments = @("scripts\build_release.py")
if ($SkipTests) {
    $Arguments += "--skip-tests"
}
if ($FullAppCheck) {
    $Arguments += "--full-app-check"
}

& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release package build failed."
}
