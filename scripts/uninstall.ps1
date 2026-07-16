[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [switch]$RemovePythonEnvironment,
    [switch]$RemoveConfig
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$KritaPluginRoot = Join-Path $env:APPDATA "krita\pykrita"
$ConfigPath = Join-Path $env:LOCALAPPDATA "krita-codex-mcp\config.json"
$Targets = @(
    (Join-Path $KritaPluginRoot "kritacodexbridge"),
    (Join-Path $KritaPluginRoot "kritacodexbridge.desktop"),
    (Join-Path $KritaPluginRoot "krita_codex_protocol")
)

try {
    foreach ($Target in $Targets) {
        if ((Test-Path -LiteralPath $Target) -and $PSCmdlet.ShouldProcess($Target, "Remove installed Krita bridge component")) {
            Remove-Item -LiteralPath $Target -Recurse -Force
            Write-Host "Removed: $Target"
        }
    }

    if ($RemovePythonEnvironment) {
        $Venv = Join-Path $RepoRoot ".venv"
        if ((Test-Path -LiteralPath $Venv) -and $PSCmdlet.ShouldProcess($Venv, "Remove project Python environment")) {
            Remove-Item -LiteralPath $Venv -Recurse -Force
            Write-Host "Removed: $Venv"
        }
    } else {
        Write-Host "Kept Python environment. Use -RemovePythonEnvironment to remove it."
    }

    if ($RemoveConfig) {
        if ((Test-Path -LiteralPath $ConfigPath) -and $PSCmdlet.ShouldProcess($ConfigPath, "Remove bridge config and token")) {
            Remove-Item -LiteralPath $ConfigPath -Force
            Write-Host "Removed: $ConfigPath"
        }
    } else {
        Write-Host "Kept config and token. Use -RemoveConfig to delete them explicitly."
    }

    Write-Host "Uninstall complete. User images, KRA files, allowed roots and exports were not touched."
} catch {
    Write-Host "Uninstall failed." -ForegroundColor Red
    Write-Host ("Cause: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
