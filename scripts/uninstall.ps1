[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [switch]$RemovePythonEnvironment,
    [switch]$RemoveConfig
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Installer = Join-Path $RepoRoot ".venv\Scripts\krita-codex-install.exe"
$Venv = Join-Path $RepoRoot ".venv"

try {
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "The package installer command is unavailable."
    }
    $Arguments = [System.Collections.Generic.List[string]]::new()
    $Arguments.Add("uninstall")
    if ($RemoveConfig) {
        $Arguments.Add("--remove-config")
    }
    & $Installer @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    if ($RemovePythonEnvironment -and $PSCmdlet.ShouldProcess($Venv, "Remove repository Python environment")) {
        Remove-Item -LiteralPath $Venv -Recurse -Force
        Write-Host "Repository Python environment removed."
    }
    exit 0
} catch {
    Write-Host "Uninstall failed." -ForegroundColor Red
    Write-Host ("Cause: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
