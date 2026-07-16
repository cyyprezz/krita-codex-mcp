function New-ConfigSwapState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigPath,

        [Parameter(Mandatory = $true)]
        [string]$TransactionRoot
    )

    return [pscustomobject]@{
        ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
        TransactionRoot = [System.IO.Path]::GetFullPath($TransactionRoot)
        BackupPath = Join-Path $TransactionRoot "config.backup.json"
        HadConfig = Test-Path -LiteralPath $ConfigPath -PathType Leaf
        BackupReady = $false
        SwapAttempted = $false
        SwapCompleted = $false
        Restored = $false
    }
}

function Assert-ConfigSwapSameVolume {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )

    $SourceRoot = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($SourcePath))
    $DestinationRoot = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($DestinationPath))
    if (-not [string]::Equals($SourceRoot, $DestinationRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Config staging and destination must be on the same volume."
    }
}

function Invoke-ConfigSwap {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$State,

        [Parameter(Mandatory = $true)]
        [string]$StageConfig
    )

    if (-not (Test-Path -LiteralPath $StageConfig -PathType Leaf)) {
        throw "Staged config is missing: $StageConfig"
    }

    Assert-ConfigSwapSameVolume -SourcePath $StageConfig -DestinationPath $State.ConfigPath
    Assert-ConfigSwapSameVolume -SourcePath $State.TransactionRoot -DestinationPath $State.ConfigPath

    if ($State.HadConfig) {
        Copy-Item -LiteralPath $State.ConfigPath -Destination $State.BackupPath -Force
        $State.BackupReady = $true
    }

    $State.SwapAttempted = $true
    if ($State.HadConfig) {
        $ReplaceBackup = Join-Path $State.TransactionRoot "config.replace-backup.json"
        [System.IO.File]::Replace($StageConfig, $State.ConfigPath, $ReplaceBackup, $true)
        if (Test-Path -LiteralPath $ReplaceBackup -PathType Leaf) {
            Remove-Item -LiteralPath $ReplaceBackup -Force
        }
    }
    else {
        [System.IO.File]::Move($StageConfig, $State.ConfigPath)
    }
    $State.SwapCompleted = $true
}

function Restore-ConfigSwap {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$State
    )

    if (-not $State.SwapAttempted) {
        return
    }

    if ($State.HadConfig) {
        if (-not $State.BackupReady -or -not (Test-Path -LiteralPath $State.BackupPath -PathType Leaf)) {
            throw "Cannot restore the previous config because its backup is unavailable."
        }

        $RestoreStage = Join-Path $State.TransactionRoot "config.restore.json"
        Copy-Item -LiteralPath $State.BackupPath -Destination $RestoreStage -Force
        Assert-ConfigSwapSameVolume -SourcePath $RestoreStage -DestinationPath $State.ConfigPath

        if (Test-Path -LiteralPath $State.ConfigPath -PathType Leaf) {
            $RestoreReplaceBackup = Join-Path $State.TransactionRoot "config.failed-replacement.json"
            [System.IO.File]::Replace($RestoreStage, $State.ConfigPath, $RestoreReplaceBackup, $true)
        }
        elseif (Test-Path -LiteralPath $State.ConfigPath) {
            throw "Cannot safely restore the previous config because the destination is not a file."
        }
        else {
            [System.IO.File]::Move($RestoreStage, $State.ConfigPath)
        }
    }
    elseif ($State.SwapCompleted -and (Test-Path -LiteralPath $State.ConfigPath -PathType Leaf)) {
        Remove-Item -LiteralPath $State.ConfigPath -Force
    }
    elseif (Test-Path -LiteralPath $State.ConfigPath) {
        throw "Cannot safely remove the newly created config because the destination is not a file."
    }

    $State.Restored = $true
}
