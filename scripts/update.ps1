[CmdletBinding()]
param(
    [switch]$SkipPythonEnvironment
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Installer = Join-Path $RepoRoot ".venv\Scripts\krita-codex-install.exe"

try {
    if (-not $SkipPythonEnvironment) {
        $Uv = Get-Command uv -ErrorAction SilentlyContinue
        if ($null -eq $Uv) {
            throw "uv is required to prepare the repository compatibility environment."
        }
        & $Uv.Source sync --no-dev --python 3.12 --project $RepoRoot
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
    }
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "The package installer command is unavailable. Rerun without -SkipPythonEnvironment."
    }
    & $Installer update
    exit $LASTEXITCODE
} catch {
    Write-Host "Update failed." -ForegroundColor Red
    Write-Host ("Cause: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
