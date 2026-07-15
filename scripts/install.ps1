[CmdletBinding()]
param(
    [string[]]$AllowedRoot = @((Join-Path $HOME "Documents")),
    [ValidateRange(1024, 65535)]
    [int]$Port = 17641,
    [switch]$SkipPythonEnvironment
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$KritaPluginRoot = Join-Path $env:APPDATA "krita\pykrita"
$PluginSource = Join-Path $RepoRoot "krita-plugin\kritacodexbridge"
$PluginDestination = Join-Path $KritaPluginRoot "kritacodexbridge"
$ProtocolSource = Join-Path $RepoRoot "src\krita_codex_protocol"
$ProtocolDestination = Join-Path $KritaPluginRoot "krita_codex_protocol"
$DesktopSource = Join-Path $RepoRoot "krita-plugin\kritacodexbridge.desktop"
$DesktopDestination = Join-Path $KritaPluginRoot "kritacodexbridge.desktop"
$ConfigDirectory = Join-Path $env:LOCALAPPDATA "krita-codex-mcp"
$ConfigPath = Join-Path $ConfigDirectory "config.json"

$ResolvedRoots = @()
foreach ($Root in $AllowedRoot) {
    if (-not [System.IO.Path]::IsPathRooted($Root)) {
        throw "AllowedRoot must be absolute: $Root"
    }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "AllowedRoot does not exist: $Root"
    }
    $ResolvedRoots += (Resolve-Path -LiteralPath $Root).Path
}

if (-not $SkipPythonEnvironment) {
    $Uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $Uv) {
        throw "uv is required. Install uv or rerun with -SkipPythonEnvironment."
    }
    & $Uv.Source sync --no-dev --python 3.12 --project $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force -Path $KritaPluginRoot | Out-Null
New-Item -ItemType Directory -Force -Path $PluginDestination | Out-Null
New-Item -ItemType Directory -Force -Path $ProtocolDestination | Out-Null
Copy-Item -Path (Join-Path $PluginSource "*") -Destination $PluginDestination -Recurse -Force
Copy-Item -Path (Join-Path $ProtocolSource "*") -Destination $ProtocolDestination -Recurse -Force
Copy-Item -LiteralPath $DesktopSource -Destination $DesktopDestination -Force

$Token = $null
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    try {
        $Existing = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
        if ($Existing.token -is [string] -and $Existing.token.Length -ge 32) {
            $Token = $Existing.token
        }
    } catch {
        Write-Warning "Existing config is invalid; generating a new bridge token."
    }
}
if ($null -eq $Token) {
    $Bytes = New-Object byte[] 32
    $Random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Random.GetBytes($Bytes)
    } finally {
        $Random.Dispose()
    }
    $Token = [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$Config = [ordered]@{
    token = $Token
    host = "127.0.0.1"
    port = $Port
    allowed_roots = $ResolvedRoots
    request_timeout_seconds = 30
    max_request_bytes = 1048576
    max_response_bytes = 16777216
    preview_max_dimension = 2048
    preview_max_bytes = 8388608
    document_max_dimension = 16384
    document_max_pixels = 67108864
}
New-Item -ItemType Directory -Force -Path $ConfigDirectory | Out-Null
$ConfigJson = $Config | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText(
    $ConfigPath,
    $ConfigJson,
    [System.Text.UTF8Encoding]::new($false)
)

$Executable = Join-Path $RepoRoot ".venv\Scripts\krita-codex-mcp.exe"
$TomlExecutable = $Executable.Replace('\', '\\')
$TomlRepoRoot = $RepoRoot.Replace('\', '\\')

Write-Host ""
Write-Host "Installed Krita plugin to: $KritaPluginRoot"
Write-Host "Wrote shared bridge config to: $ConfigPath"
Write-Host ""
Write-Host "Enable 'Krita Codex Bridge' in Krita's Python Plugin Manager, then restart Krita."
Write-Host "Add this to your Codex config.toml (the installer does not edit it):"
Write-Host ""
Write-Host "[mcp_servers.krita]"
Write-Host "command = `"$TomlExecutable`""
Write-Host "cwd = `"$TomlRepoRoot`""
Write-Host "startup_timeout_sec = 10"
Write-Host "tool_timeout_sec = 120"
