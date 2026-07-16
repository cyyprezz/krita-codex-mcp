[CmdletBinding()]
param(
    [switch]$SkipPythonEnvironment
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Arguments = @{}
if ($SkipPythonEnvironment) {
    $Arguments["SkipPythonEnvironment"] = $true
}

Write-Host "Updating krita-codex-mcp while preserving the existing config and token..."
& (Join-Path $PSScriptRoot "install.ps1") @Arguments
$InstallExitCode = $LASTEXITCODE
if ($InstallExitCode -ne 0) {
    Write-Host "Update failed; the existing installation was kept where available." -ForegroundColor Red
    exit $InstallExitCode
}
Write-Host "Update complete. Existing config and token were kept."
exit 0
