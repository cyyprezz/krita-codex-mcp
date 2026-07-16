[CmdletBinding()]
param(
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Installer = Join-Path $RepoRoot ".venv\Scripts\krita-codex-install.exe"

if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    Write-Host "Installation check failed." -ForegroundColor Red
    Write-Host "Run scripts\install.ps1 to prepare the package command." -ForegroundColor Yellow
    exit 1
}

$Arguments = [System.Collections.Generic.List[string]]::new()
$Arguments.Add("check")
if ($Offline) {
    $Arguments.Add("--offline")
}
& $Installer @Arguments
exit $LASTEXITCODE
