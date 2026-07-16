[CmdletBinding()]
param(
    [string[]]$AllowedRoot,
    [ValidateRange(1024, 65535)]
    [int]$Port = 17641,
    [switch]$SkipPythonEnvironment,
    [switch]$ResetConfig
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

    $CliArguments = [System.Collections.Generic.List[string]]::new()
    $CliArguments.Add("install")
    if ($PSBoundParameters.ContainsKey("AllowedRoot")) {
        foreach ($Root in $AllowedRoot) {
            $CliArguments.Add("--allowed-root")
            $CliArguments.Add($Root)
        }
    }
    if ($PSBoundParameters.ContainsKey("Port")) {
        $CliArguments.Add("--port")
        $CliArguments.Add([string]$Port)
    }
    if ($ResetConfig) {
        $CliArguments.Add("--reset-config")
    }

    & $Installer @CliArguments
    exit $LASTEXITCODE
} catch {
    Write-Host "Installation failed." -ForegroundColor Red
    Write-Host ("Cause: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
