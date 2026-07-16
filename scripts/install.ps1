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

. (Join-Path $PSScriptRoot "install-config-helpers.ps1")

function New-BridgeToken {
    $Bytes = New-Object byte[] 32
    $Random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Random.GetBytes($Bytes)
    } finally {
        $Random.Dispose()
    }
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function ConvertTo-OrderedMap {
    param([Parameter(Mandatory = $true)]$InputObject)

    $Result = [ordered]@{}
    foreach ($Property in $InputObject.PSObject.Properties) {
        $Result[$Property.Name] = $Property.Value
    }
    return $Result
}

function Resolve-AllowedRoots {
    param([Parameter(Mandatory = $true)][object[]]$Roots)

    if ($Roots.Count -eq 0) {
        throw "At least one AllowedRoot is required."
    }
    $Resolved = @()
    foreach ($RootValue in $Roots) {
        $Root = [string]$RootValue
        if ([string]::IsNullOrWhiteSpace($Root)) {
            throw "AllowedRoot must not be empty."
        }
        if (-not [System.IO.Path]::IsPathRooted($Root)) {
            throw "AllowedRoot must be absolute: $Root"
        }
        if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
            throw "AllowedRoot does not exist: $Root"
        }
        $Resolved += (Resolve-Path -LiteralPath $Root).Path
    }
    return $Resolved
}

function Set-DefaultValue {
    param(
        [Parameter(Mandatory = $true)]$Map,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Value
    )
    if (-not $Map.Contains($Name)) {
        $Map[$Name] = $Value
    }
}

function Assert-InstalledFiles {
    param(
        [Parameter(Mandatory = $true)][string]$PluginPath,
        [Parameter(Mandatory = $true)][string]$ProtocolPath,
        [Parameter(Mandatory = $true)][string]$DesktopPath,
        [Parameter(Mandatory = $true)][string]$ExecutablePath
    )

    foreach ($Required in @(
        (Join-Path $PluginPath "__init__.py"),
        (Join-Path $PluginPath "extension.py"),
        (Join-Path $PluginPath "http_bridge.py"),
        (Join-Path $PluginPath "http_server.py"),
        (Join-Path $ProtocolPath "__init__.py"),
        (Join-Path $ProtocolPath "config.py"),
        (Join-Path $ProtocolPath "messages.py"),
        $DesktopPath,
        $ExecutablePath
    )) {
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
            throw "Required installed file is missing: $Required"
        }
    }
}

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
$Executable = Join-Path $RepoRoot ".venv\Scripts\krita-codex-mcp.exe"
$PythonExecutable = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$AllowedRootWasSet = $PSBoundParameters.ContainsKey("AllowedRoot")
$PortWasSet = $PSBoundParameters.ContainsKey("Port")

try {
    foreach ($RequiredSource in @(
        (Join-Path $PluginSource "__init__.py"),
        (Join-Path $PluginSource "extension.py"),
        (Join-Path $ProtocolSource "__init__.py"),
        $DesktopSource
    )) {
        if (-not (Test-Path -LiteralPath $RequiredSource -PathType Leaf)) {
            throw "Repository installation source is incomplete: $RequiredSource"
        }
    }

    if (-not $SkipPythonEnvironment) {
        $Uv = Get-Command uv -ErrorAction SilentlyContinue
        if ($null -eq $Uv) {
            throw "uv is required. Install uv or use -SkipPythonEnvironment only with an existing .venv."
        }
        & $Uv.Source sync --no-dev --python 3.12 --project $RepoRoot
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "MCP executable is missing at $Executable. Rerun without -SkipPythonEnvironment."
    }
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python environment is incomplete at $PythonExecutable. Rerun without -SkipPythonEnvironment."
    }

    $ExistingConfig = $null
    if ((Test-Path -LiteralPath $ConfigPath -PathType Leaf) -and -not $ResetConfig) {
        try {
            $ExistingConfig = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
        } catch {
            throw "Existing config is invalid and was not changed. Fix it or rerun with -ResetConfig. Cause: $($_.Exception.Message)"
        }
    }

    if ($null -ne $ExistingConfig) {
        $Config = ConvertTo-OrderedMap $ExistingConfig
        if (-not $Config.Contains("token") -or
            -not ($Config["token"] -is [string]) -or
            $Config["token"].Length -lt 32) {
            throw "Existing config has no valid token and was not changed. Fix it or rerun with -ResetConfig."
        }
        if ($Config.Contains("host") -and $Config["host"] -ne "127.0.0.1") {
            throw "Existing config host must be exactly 127.0.0.1 and was not changed."
        }
        $Config["host"] = "127.0.0.1"
        if ($AllowedRootWasSet) {
            $Config["allowed_roots"] = @(Resolve-AllowedRoots $AllowedRoot)
        } elseif (-not $Config.Contains("allowed_roots")) {
            throw "Existing config has no allowed_roots and was not changed."
        } else {
            $Config["allowed_roots"] = @(Resolve-AllowedRoots @($Config["allowed_roots"]))
        }
        if ($PortWasSet) {
            $Config["port"] = $Port
        }
        Set-DefaultValue $Config "port" 17641
    } else {
        $RootsToUse = if ($AllowedRootWasSet) { $AllowedRoot } else { @((Join-Path $HOME "Documents")) }
        $Config = [ordered]@{
            token = New-BridgeToken
            host = "127.0.0.1"
            port = $Port
            allowed_roots = @(Resolve-AllowedRoots $RootsToUse)
        }
    }

    Set-DefaultValue $Config "request_timeout_seconds" 30
    Set-DefaultValue $Config "open_save_timeout_seconds" 120
    Set-DefaultValue $Config "export_timeout_seconds" 180
    Set-DefaultValue $Config "max_request_bytes" 1048576
    Set-DefaultValue $Config "max_response_bytes" 16777216
    Set-DefaultValue $Config "preview_max_dimension" 2048
    Set-DefaultValue $Config "preview_max_bytes" 8388608
    Set-DefaultValue $Config "asset_max_bytes" 67108864
    Set-DefaultValue $Config "asset_batch_max_bytes" 268435456
    Set-DefaultValue $Config "asset_max_frames" 256
    Set-DefaultValue $Config "asset_render_max_pixels" 134217728
    Set-DefaultValue $Config "document_max_dimension" 16384
    Set-DefaultValue $Config "document_max_pixels" 67108864

    New-Item -ItemType Directory -Force -Path $KritaPluginRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $ConfigDirectory | Out-Null
    $TransactionRoot = Join-Path $KritaPluginRoot (".kritacodex-install-" + [Guid]::NewGuid().ToString("N"))
    $ConfigTransactionRoot = Join-Path $ConfigDirectory (".kritacodex-config-" + [Guid]::NewGuid().ToString("N"))
    $StagePlugin = Join-Path $TransactionRoot "stage\kritacodexbridge"
    $StageProtocol = Join-Path $TransactionRoot "stage\krita_codex_protocol"
    $StageDesktop = Join-Path $TransactionRoot "stage\kritacodexbridge.desktop"
    $BackupPlugin = Join-Path $TransactionRoot "backup\kritacodexbridge"
    $BackupProtocol = Join-Path $TransactionRoot "backup\krita_codex_protocol"
    $BackupDesktop = Join-Path $TransactionRoot "backup\kritacodexbridge.desktop"
    $StageConfig = Join-Path $ConfigTransactionRoot "config.staged.json"
    $HadPlugin = Test-Path -LiteralPath $PluginDestination
    $HadProtocol = Test-Path -LiteralPath $ProtocolDestination
    $HadDesktop = Test-Path -LiteralPath $DesktopDestination
    $InstalledPlugin = $false
    $InstalledProtocol = $false
    $InstalledDesktop = $false
    $KeepTransaction = $false
    $KeepConfigTransaction = $false
    $ConfigSwapState = New-ConfigSwapState -ConfigPath $ConfigPath -TransactionRoot $ConfigTransactionRoot

    try {
        New-Item -ItemType Directory -Force -Path $StagePlugin, $StageProtocol | Out-Null
        New-Item -ItemType Directory -Force -Path (Split-Path $BackupPlugin -Parent) | Out-Null
        New-Item -ItemType Directory -Force -Path $ConfigTransactionRoot | Out-Null
        Copy-Item -Path (Join-Path $PluginSource "*") -Destination $StagePlugin -Recurse -Force
        Copy-Item -Path (Join-Path $ProtocolSource "*") -Destination $StageProtocol -Recurse -Force
        Copy-Item -LiteralPath $DesktopSource -Destination $StageDesktop -Force

        foreach ($StagedFile in @(
            (Join-Path $StagePlugin "__init__.py"),
            (Join-Path $StagePlugin "http_bridge.py"),
            (Join-Path $StageProtocol "config.py"),
            $StageDesktop
        )) {
            if (-not (Test-Path -LiteralPath $StagedFile -PathType Leaf)) {
                throw "Staged installation is incomplete: $StagedFile"
            }
        }

        $ConfigJson = $Config | ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText(
            $StageConfig,
            $ConfigJson,
            [System.Text.UTF8Encoding]::new($false)
        )
        $HadConfigOverride = Test-Path Env:KRITA_CODEX_CONFIG
        $PreviousConfigOverride = $env:KRITA_CODEX_CONFIG
        try {
            $env:KRITA_CODEX_CONFIG = $StageConfig
            $ConfigValidationOutput = & $PythonExecutable -c "import sys;sys.excepthook=lambda t,v,b:print(t.__name__+': '+str(v));from krita_codex_protocol import BridgeConfig;BridgeConfig.load()" 2>&1
            $ConfigValidationExitCode = $LASTEXITCODE
        } finally {
            if ($HadConfigOverride) {
                $env:KRITA_CODEX_CONFIG = $PreviousConfigOverride
            } else {
                Remove-Item Env:KRITA_CODEX_CONFIG -ErrorAction SilentlyContinue
            }
        }
        if ($ConfigValidationExitCode -ne 0) {
            throw ("Generated config is invalid: " + [string]$ConfigValidationOutput)
        }

        if ($HadPlugin) { Move-Item -LiteralPath $PluginDestination -Destination $BackupPlugin }
        Move-Item -LiteralPath $StagePlugin -Destination $PluginDestination
        $InstalledPlugin = $true

        if ($HadProtocol) { Move-Item -LiteralPath $ProtocolDestination -Destination $BackupProtocol }
        Move-Item -LiteralPath $StageProtocol -Destination $ProtocolDestination
        $InstalledProtocol = $true

        if ($HadDesktop) { Move-Item -LiteralPath $DesktopDestination -Destination $BackupDesktop }
        Move-Item -LiteralPath $StageDesktop -Destination $DesktopDestination
        $InstalledDesktop = $true

        Invoke-ConfigSwap -State $ConfigSwapState -StageConfig $StageConfig

        Assert-InstalledFiles $PluginDestination $ProtocolDestination $DesktopDestination $Executable
    } catch {
        $InstallFailure = $_.Exception.Message
        $RollbackErrors = @()
        $RollbackSteps = @(
            @{
                Name = "previous config"
                Action = {
                    Restore-ConfigSwap -State $ConfigSwapState
                }
            },
            @{
                Name = "new plugin"
                Action = {
                    if ($InstalledPlugin -and (Test-Path -LiteralPath $PluginDestination)) {
                        Remove-Item -LiteralPath $PluginDestination -Recurse -Force
                    }
                }
            },
            @{
                Name = "previous plugin"
                Action = {
                    if ($HadPlugin -and (Test-Path -LiteralPath $BackupPlugin)) {
                        Move-Item -LiteralPath $BackupPlugin -Destination $PluginDestination
                    }
                }
            },
            @{
                Name = "new protocol package"
                Action = {
                    if ($InstalledProtocol -and (Test-Path -LiteralPath $ProtocolDestination)) {
                        Remove-Item -LiteralPath $ProtocolDestination -Recurse -Force
                    }
                }
            },
            @{
                Name = "previous protocol package"
                Action = {
                    if ($HadProtocol -and (Test-Path -LiteralPath $BackupProtocol)) {
                        Move-Item -LiteralPath $BackupProtocol -Destination $ProtocolDestination
                    }
                }
            },
            @{
                Name = "new desktop file"
                Action = {
                    if ($InstalledDesktop -and (Test-Path -LiteralPath $DesktopDestination)) {
                        Remove-Item -LiteralPath $DesktopDestination -Force
                    }
                }
            },
            @{
                Name = "previous desktop file"
                Action = {
                    if ($HadDesktop -and (Test-Path -LiteralPath $BackupDesktop)) {
                        Move-Item -LiteralPath $BackupDesktop -Destination $DesktopDestination
                    }
                }
            }
        )
        foreach ($Step in $RollbackSteps) {
            try {
                & $Step.Action
            } catch {
                $RollbackErrors += ("{0}: {1}" -f $Step.Name, $_.Exception.Message)
            }
        }
        if ($RollbackErrors.Count -gt 0) {
            $KeepTransaction = $true
            $KeepConfigTransaction = $true
            throw ("Installation failed and rollback was incomplete. Backups retained at {0} and {1}. Cause: {2}. Rollback errors: {3}" -f $TransactionRoot, $ConfigTransactionRoot, $InstallFailure, ($RollbackErrors -join "; "))
        }
        throw "Installation failed; the previous plugin state was restored. Cause: $InstallFailure"
    } finally {
        if (-not $KeepTransaction -and (Test-Path -LiteralPath $TransactionRoot)) {
            Remove-Item -LiteralPath $TransactionRoot -Recurse -Force
        }
        if (-not $KeepConfigTransaction -and (Test-Path -LiteralPath $ConfigTransactionRoot)) {
            Remove-Item -LiteralPath $ConfigTransactionRoot -Recurse -Force
        }
    }

    $TomlExecutable = $Executable.Replace('\', '\\')
    $TomlRepoRoot = $RepoRoot.Replace('\', '\\')

    Write-Host ""
    Write-Host "Installation complete."
    Write-Host "Plugin: $PluginDestination"
    Write-Host "Protocol: $ProtocolDestination"
    Write-Host "Configuration: $ConfigPath"
    if ($null -ne $ExistingConfig -and -not $ResetConfig) {
        Write-Host "Existing token and local configuration were preserved."
    }
    Write-Host ""
    Write-Host "Enable 'Krita Codex Bridge' in Krita's Python Plugin Manager, then restart Krita."
    Write-Host "After restart, run: .\scripts\check-installation.ps1"
    Write-Host "The installer did not edit Codex configuration. Use this block:"
    Write-Host ""
    Write-Host "[mcp_servers.krita]"
    Write-Host "command = `"$TomlExecutable`""
    Write-Host "cwd = `"$TomlRepoRoot`""
    Write-Host "startup_timeout_sec = 10"
    Write-Host "tool_timeout_sec = 210"
} catch {
    Write-Host "Installation failed." -ForegroundColor Red
    Write-Host ("Cause: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "Fix the reported cause and rerun scripts\install.ps1. Existing config is preserved unless -ResetConfig is explicit."
    exit 1
}
exit 0
