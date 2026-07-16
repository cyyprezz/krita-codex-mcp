[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Statuses = [ordered]@{
    Configuration = "NOT RUN"
    "Plugin installation" = "NOT RUN"
    Bridge = "NOT RUN"
    Authentication = "NOT RUN"
    MCP = "NOT RUN"
    Preview = "NOT RUN"
    Export = "NOT RUN"
}
$FailureStage = $null
$FailureCause = $null
$FailureFix = $null
$TemporaryDocumentId = $null
$TemporaryExportPath = $null
$OriginalActiveDocumentId = $null
$CaughtFailure = $null
$CleanupBusyWaitSeconds = 30.0

function Stop-InstallationCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Cause,
        [Parameter(Mandatory = $true)][string]$Fix
    )
    $script:Statuses[$Stage] = "FAILED"
    $script:FailureStage = $Stage
    $script:FailureCause = $Cause
    $script:FailureFix = $Fix
    throw [System.InvalidOperationException]::new($Cause)
}

function Write-InstallationSummary {
    Write-Host ""
    foreach ($Entry in $script:Statuses.GetEnumerator()) {
        Write-Host ("{0}: {1}" -f $Entry.Key, $Entry.Value)
    }
    Write-Host ""
}

function Test-PngSignature {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Description
    )
    [byte[]]$Expected = 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A
    if ($Bytes.Length -lt $Expected.Length) {
        throw "$Description is shorter than the PNG signature."
    }
    for ($Index = 0; $Index -lt $Expected.Length; $Index++) {
        if ($Bytes[$Index] -ne $Expected[$Index]) {
            throw "$Description does not have a valid PNG signature."
        }
    }
}

function Get-ObjectPropertyValue {
    param(
        [Parameter(Mandatory = $true)]$InputObject,
        [Parameter(Mandatory = $true)][string]$Name,
        $Default = $null
    )
    $Property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $Property) {
        return $Default
    }
    return $Property.Value
}

function Invoke-BridgeRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)]$Arguments,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][double]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][int64]$MaxResponseBytes
    )

    $Envelope = [ordered]@{
        protocol_version = "1"
        request_id = [Guid]::NewGuid().ToString()
        operation = $Operation
        arguments = $Arguments
    }
    $Body = [System.Text.Encoding]::UTF8.GetBytes(($Envelope | ConvertTo-Json -Depth 12 -Compress))
    $Request = [System.Net.HttpWebRequest]::Create($Url)
    $Request.Method = "POST"
    $Request.Proxy = $null
    $Request.AllowAutoRedirect = $false
    $Request.ContentType = "application/json"
    $Request.Accept = "application/json"
    $Request.Headers["Authorization"] = "Bearer $Token"
    $TimeoutMilliseconds = [int][Math]::Min(2147483647, [Math]::Ceiling($TimeoutSeconds * 1000) + 5000)
    $Request.Timeout = $TimeoutMilliseconds
    $Request.ReadWriteTimeout = $TimeoutMilliseconds
    $Request.ContentLength = $Body.Length

    $RequestStream = $Request.GetRequestStream()
    try {
        $RequestStream.Write($Body, 0, $Body.Length)
    } finally {
        $RequestStream.Dispose()
    }

    $Response = $null
    try {
        try {
            $Response = $Request.GetResponse()
        } catch [System.Net.WebException] {
            if ($null -eq $_.Exception.Response) {
                throw
            }
            $Response = $_.Exception.Response
        }
        $ResponseStream = $Response.GetResponseStream()
        $Buffer = New-Object byte[] 8192
        $ResponseBuffer = New-Object System.IO.MemoryStream
        $RemainingResponseBytes = $MaxResponseBytes + 1
        try {
            while ($RemainingResponseBytes -gt 0) {
                $ReadCount = $ResponseStream.Read(
                    $Buffer,
                    0,
                    [int][Math]::Min([int64]$Buffer.Length, $RemainingResponseBytes)
                )
                if ($ReadCount -eq 0) {
                    break
                }
                $ResponseBuffer.Write($Buffer, 0, $ReadCount)
                $RemainingResponseBytes -= $ReadCount
            }
            [byte[]]$ResponseBytes = $ResponseBuffer.ToArray()
        } finally {
            $ResponseBuffer.Dispose()
            $ResponseStream.Dispose()
        }
    } finally {
        if ($null -ne $Response) {
            $Response.Dispose()
        }
    }

    if ($ResponseBytes.Length -gt $MaxResponseBytes) {
        throw "Bridge response exceeds the configured maximum size."
    }
    $ResponseText = [System.Text.Encoding]::UTF8.GetString($ResponseBytes)
    try {
        $Payload = $ResponseText | ConvertFrom-Json
    } catch {
        throw "Bridge returned invalid JSON: $($_.Exception.Message)"
    }
    if ($Payload.ok -ne $true) {
        $ErrorObject = Get-ObjectPropertyValue $Payload "error"
        $Code = [string](Get-ObjectPropertyValue $ErrorObject "code" "UNKNOWN")
        $Message = [string](Get-ObjectPropertyValue $ErrorObject "message" "No message")
        $Details = Get-ObjectPropertyValue $ErrorObject "details"
        $DetailText = if ($null -ne $Details) {
            " Details: " + ($Details | ConvertTo-Json -Depth 6 -Compress)
        } else {
            ""
        }
        $BridgeException = [System.InvalidOperationException]::new(
            "Bridge returned $Code`: $Message.$DetailText"
        )
        $BridgeException.Data["BridgeCode"] = $Code
        $OperationMayComplete = $false
        if ($null -ne $Details) {
            $OperationMayComplete = [bool](
                Get-ObjectPropertyValue $Details "operation_may_complete" $false
            )
        }
        $BridgeException.Data["OperationMayComplete"] = $OperationMayComplete
        if ($OperationMayComplete) {
            $script:CleanupBusyWaitSeconds = [Math]::Max(
                $script:CleanupBusyWaitSeconds,
                $TimeoutSeconds
            )
        }
        throw $BridgeException
    }
    if ($Payload.request_id -ne $Envelope.request_id) {
        throw "Bridge response request_id does not match the request."
    }
    return $Payload.result
}

try {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    if ($env:OS -ne "Windows_NT") {
        Stop-InstallationCheck "Configuration" "This preflight supports Windows only." "Run it on the Windows account that runs Krita and Codex."
    }
    if ([string]::IsNullOrWhiteSpace($env:APPDATA) -or [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Stop-InstallationCheck "Configuration" "APPDATA or LOCALAPPDATA is missing." "Use a normal interactive Windows user session."
    }

    $ConfigPath = Join-Path $env:LOCALAPPDATA "krita-codex-mcp\config.json"
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        Stop-InstallationCheck "Configuration" "Config not found at $ConfigPath." "Run .\scripts\install.ps1 first."
    }
    try {
        $Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
    } catch {
        Stop-InstallationCheck "Configuration" "Config is not valid JSON: $($_.Exception.Message)" "Repair the file or rerun install.ps1 with -ResetConfig."
    }
    $Token = Get-ObjectPropertyValue $Config "token"
    if (-not ($Token -is [string]) -or $Token.Length -lt 32) {
        Stop-InstallationCheck "Configuration" "Token is missing or shorter than 32 characters." "Rerun install.ps1 with -ResetConfig to create a new token."
    }
    $HostValue = Get-ObjectPropertyValue $Config "host" "127.0.0.1"
    if ($HostValue -ne "127.0.0.1") {
        Stop-InstallationCheck "Configuration" "Bridge host must be exactly 127.0.0.1." "Set host to 127.0.0.1; network exposure is not supported."
    }
    $Port = [int](Get-ObjectPropertyValue $Config "port" 17641)
    if ($Port -lt 1024 -or $Port -gt 65535) {
        Stop-InstallationCheck "Configuration" "Configured port is outside 1024-65535." "Rerun install.ps1 with a valid -Port value."
    }
    $AllowedRoots = @(Get-ObjectPropertyValue $Config "allowed_roots" @())
    if ($AllowedRoots.Count -eq 0) {
        Stop-InstallationCheck "Configuration" "No allowed_roots are configured." "Rerun install.ps1 with at least one narrow -AllowedRoot."
    }
    $ResolvedRoots = @()
    foreach ($RootValue in $AllowedRoots) {
        $Root = [string]$RootValue
        if (-not [System.IO.Path]::IsPathRooted($Root)) {
            Stop-InstallationCheck "Configuration" "Allowed root is not absolute: $Root" "Use an existing absolute Windows directory."
        }
        if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
            Stop-InstallationCheck "Configuration" "Allowed root does not exist: $Root" "Create the directory or update allowed_roots."
        }
        $Resolved = (Resolve-Path -LiteralPath $Root).Path
        $WriteProbe = Join-Path $Resolved (".krita-codex-write-test-" + [Guid]::NewGuid().ToString("N") + ".tmp")
        try {
            [System.IO.File]::WriteAllText($WriteProbe, "local preflight")
            Remove-Item -LiteralPath $WriteProbe -Force
        } catch {
            if (Test-Path -LiteralPath $WriteProbe) { Remove-Item -LiteralPath $WriteProbe -Force -ErrorAction SilentlyContinue }
            Stop-InstallationCheck "Configuration" "Allowed root is not writable: $Resolved" "Grant this Windows user write access or choose another narrow allowed root."
        }
        $ResolvedRoots += $Resolved
    }
    $RequestTimeout = [double](Get-ObjectPropertyValue $Config "request_timeout_seconds" 30.0)
    $ExportTimeout = [double](Get-ObjectPropertyValue $Config "export_timeout_seconds" 180.0)
    $MaxResponseBytes = [int64](Get-ObjectPropertyValue $Config "max_response_bytes" 16777216)
    $Statuses["Configuration"] = "OK"

    $KritaPluginRoot = Join-Path $env:APPDATA "krita\pykrita"
    $PluginDestination = Join-Path $KritaPluginRoot "kritacodexbridge"
    $ProtocolDestination = Join-Path $KritaPluginRoot "krita_codex_protocol"
    foreach ($Required in @(
        (Join-Path $PluginDestination "__init__.py"),
        (Join-Path $PluginDestination "extension.py"),
        (Join-Path $PluginDestination "http_bridge.py"),
        (Join-Path $PluginDestination "http_server.py"),
        (Join-Path $ProtocolDestination "__init__.py"),
        (Join-Path $ProtocolDestination "config.py"),
        (Join-Path $ProtocolDestination "messages.py"),
        (Join-Path $KritaPluginRoot "kritacodexbridge.desktop")
    )) {
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
            Stop-InstallationCheck "Plugin installation" "Installed file is missing: $Required" "Rerun .\scripts\update.ps1, then enable the plugin and restart Krita."
        }
    }
    $Statuses["Plugin installation"] = "OK"

    $Executable = Join-Path $RepoRoot ".venv\Scripts\krita-codex-mcp.exe"
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        Stop-InstallationCheck "MCP" "MCP executable is missing: $Executable" "Rerun install.ps1 without -SkipPythonEnvironment."
    }
    if ((Get-Item -LiteralPath $Executable).Length -eq 0) {
        Stop-InstallationCheck "MCP" "MCP executable is empty: $Executable" "Rebuild the local environment with install.ps1."
    }
    $PythonExecutable = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        Stop-InstallationCheck "MCP" "Python environment is incomplete: $PythonExecutable" "Rebuild the local environment with install.ps1."
    }
    $HadConfigOverride = Test-Path Env:KRITA_CODEX_CONFIG
    $PreviousConfigOverride = $env:KRITA_CODEX_CONFIG
    try {
        $env:KRITA_CODEX_CONFIG = $ConfigPath
        $ValidationOutput = & $PythonExecutable -c "import sys;sys.excepthook=lambda t,v,b:print(t.__name__+': '+str(v));from krita_codex_protocol import BridgeConfig;BridgeConfig.load()" 2>&1
    } finally {
        if ($HadConfigOverride) {
            $env:KRITA_CODEX_CONFIG = $PreviousConfigOverride
        } else {
            Remove-Item Env:KRITA_CODEX_CONFIG -ErrorAction SilentlyContinue
        }
    }
    if ($LASTEXITCODE -ne 0) {
        Stop-InstallationCheck "Configuration" ("Shared config validation failed: " + [string]$ValidationOutput) "Repair config.json or rerun install.ps1 with -ResetConfig."
    }
    $ImportOutput = & $PythonExecutable -c "import krita_codex_mcp.server" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-InstallationCheck "MCP" ("MCP package import failed: " + [string]$ImportOutput) "Rebuild the local environment with install.ps1."
    }
    $Statuses["MCP"] = "OK"

    $TcpClient = New-Object System.Net.Sockets.TcpClient
    try {
        $Connect = $TcpClient.BeginConnect("127.0.0.1", $Port, $null, $null)
        try {
            if (-not $Connect.AsyncWaitHandle.WaitOne(2000)) {
                throw "Timed out connecting to 127.0.0.1:$Port."
            }
            $TcpClient.EndConnect($Connect)
        } finally {
            $Connect.AsyncWaitHandle.Close()
        }
    } catch {
        Stop-InstallationCheck "Bridge" $_.Exception.Message "Enable 'Krita Codex Bridge', restart Krita, and verify that the configured port is free."
    } finally {
        $TcpClient.Dispose()
    }
    $Statuses["Bridge"] = "OK"

    $BridgeUrl = "http://127.0.0.1:$Port/v1/execute"
    try {
        $DocumentsBefore = Invoke-BridgeRequest "document" @{ action = "list" } $BridgeUrl $Token $RequestTimeout $MaxResponseBytes
        $OriginalActiveDocumentId = [string](Get-ObjectPropertyValue $DocumentsBefore "active_document_id")
    } catch {
        if ($_.Exception.Message.Contains("UNAUTHORIZED")) {
            Stop-InstallationCheck "Authentication" $_.Exception.Message "Run update.ps1 and restart Krita so the plugin and MCP use the same token."
        }
        Stop-InstallationCheck "Bridge" $_.Exception.Message "Wait for a running Krita operation to finish, or restart Krita if the bridge does not recover."
    }
    $Statuses["Authentication"] = "OK"

    $PreflightId = [Guid]::NewGuid().ToString("N")
    try {
        $Created = Invoke-BridgeRequest "document" @{
            action = "create"
            width = 64
            height = 64
            name = "Krita Codex Preflight $PreflightId"
            color_model = "RGBA"
            color_depth = "U8"
            profile = ""
            resolution = 72
        } $BridgeUrl $Token $RequestTimeout $MaxResponseBytes
        $TemporaryDocumentId = [string]$Created.affected_document_id
        if ([string]::IsNullOrWhiteSpace($TemporaryDocumentId)) {
            throw "Temporary document creation returned no document ID."
        }
        $null = Invoke-BridgeRequest "state" @{ document_id = $TemporaryDocumentId } $BridgeUrl $Token $RequestTimeout $MaxResponseBytes
    } catch {
        Stop-InstallationCheck "Bridge" $_.Exception.Message "Restart Krita and rerun the preflight with the current installed plugin."
    }

    try {
        $Preview = Invoke-BridgeRequest "preview" @{
            max_width = 64
            max_height = 64
            scale = 1
            document_id = $TemporaryDocumentId
        } $BridgeUrl $Token $RequestTimeout $MaxResponseBytes
        $PreviewBytes = [Convert]::FromBase64String([string]$Preview.png_base64)
        Test-PngSignature $PreviewBytes "Preview response"
    } catch {
        Stop-InstallationCheck "Preview" $_.Exception.Message "Update the plugin, restart Krita, and check the bridge log."
    }
    $Statuses["Preview"] = "OK"

    $TemporaryExportPath = Join-Path $ResolvedRoots[0] (".krita-codex-preflight-" + $PreflightId + ".png")
    try {
        $null = Invoke-BridgeRequest "save_export" @{
            png_path = $TemporaryExportPath
            overwrite = $false
            document_id = $TemporaryDocumentId
        } $BridgeUrl $Token $ExportTimeout $MaxResponseBytes
        if (-not (Test-Path -LiteralPath $TemporaryExportPath -PathType Leaf)) {
            throw "Krita reported success but did not create $TemporaryExportPath."
        }
        Test-PngSignature ([System.IO.File]::ReadAllBytes($TemporaryExportPath)) "Exported file"
    } catch {
        Stop-InstallationCheck "Export" $_.Exception.Message "Verify allowed_roots is writable, update the plugin, and restart Krita."
    }
    $Statuses["Export"] = "OK"
} catch {
    $CaughtFailure = $_
    if ($null -eq $FailureCause) {
        $FailureStage = "Configuration"
        $FailureCause = $_.Exception.Message
        $FailureFix = "Fix the reported local installation problem and rerun this script."
        $Statuses[$FailureStage] = "FAILED"
    }
} finally {
    if (-not [string]::IsNullOrWhiteSpace($TemporaryDocumentId)) {
        $CloseFailure = $null
        $CloseDeadline = [DateTime]::UtcNow.AddSeconds($CleanupBusyWaitSeconds)
        do {
            try {
                $null = Invoke-BridgeRequest "document" @{
                    action = "close"
                    document_id = $TemporaryDocumentId
                    discard_changes = $true
                } $BridgeUrl $Token $RequestTimeout $MaxResponseBytes
                $CloseFailure = $null
                break
            } catch {
                $CloseFailure = $_
                if ($_.Exception.Message.Contains("BRIDGE_BUSY") -and [DateTime]::UtcNow -lt $CloseDeadline) {
                    Start-Sleep -Seconds 1
                    continue
                }
                break
            }
        } while ([DateTime]::UtcNow -lt $CloseDeadline)
        if ($null -ne $CloseFailure) {
            if ($null -eq $CaughtFailure) {
                $CaughtFailure = $CloseFailure
                $FailureStage = "Bridge"
                $FailureCause = "Temporary document could not be closed: $($CloseFailure.Exception.Message)"
                $FailureFix = "Close the 'Krita Codex Preflight' document manually, restart Krita, and rerun the preflight."
                $Statuses["Bridge"] = "FAILED"
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($OriginalActiveDocumentId)) {
        try {
            $null = Invoke-BridgeRequest "document" @{
                action = "activate"
                document_id = $OriginalActiveDocumentId
            } $BridgeUrl $Token $RequestTimeout $MaxResponseBytes
        } catch {
            if ($null -eq $CaughtFailure) {
                $CaughtFailure = $_
                $FailureStage = "Bridge"
                $FailureCause = "Original active document could not be restored: $($_.Exception.Message)"
                $FailureFix = "Reactivate the original document in Krita and rerun the preflight."
                $Statuses["Bridge"] = "FAILED"
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($TemporaryExportPath) -and (Test-Path -LiteralPath $TemporaryExportPath)) {
        try {
            Remove-Item -LiteralPath $TemporaryExportPath -Force
        } catch {
            if ($null -eq $CaughtFailure) {
                $CaughtFailure = $_
                $FailureStage = "Export"
                $FailureCause = "Temporary export could not be removed: $TemporaryExportPath"
                $FailureFix = "Remove that single preflight PNG manually and check directory permissions."
                $Statuses["Export"] = "FAILED"
            }
        }
    }
}

Write-InstallationSummary
if ($null -ne $CaughtFailure) {
    Write-Host ("Cause: " + $FailureCause) -ForegroundColor Red
    Write-Host ("Fix: " + $FailureFix) -ForegroundColor Yellow
    exit 1
}

Write-Host "Ready for productive use." -ForegroundColor Green
exit 0
