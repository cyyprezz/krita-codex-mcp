from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class InstallScriptContractTests(unittest.TestCase):
    def test_installer_is_transactional_repeatable_and_preserves_config(self) -> None:
        source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("$StagePlugin", source)
        self.assertIn("$BackupPlugin", source)
        self.assertIn("$ConfigTransactionRoot = Join-Path $ConfigDirectory", source)
        self.assertIn("Invoke-ConfigSwap -State $ConfigSwapState", source)
        self.assertIn("Restore-ConfigSwap -State $ConfigSwapState", source)
        self.assertIn("$KeepConfigTransaction = $true", source)
        self.assertIn("previous plugin state was restored", source)
        self.assertIn("Existing token and local configuration were preserved", source)
        self.assertIn('Set-DefaultValue $Config "open_save_timeout_seconds" 120', source)
        self.assertIn('Set-DefaultValue $Config "export_timeout_seconds" 180', source)
        self.assertIn('Write-Host "tool_timeout_sec = 210"', source)

    def test_preflight_covers_local_bridge_preview_export_and_cleanup(self) -> None:
        source = (SCRIPTS / "check-installation.ps1").read_text(encoding="utf-8")
        for operation in ('"state"', '"preview"', '"save_export"'):
            self.assertIn(f"Invoke-BridgeRequest {operation}", source)
        self.assertIn("Test-PngSignature", source)
        self.assertIn('action = "close"', source)
        self.assertIn("Remove-Item -LiteralPath $TemporaryExportPath", source)
        self.assertIn('BridgeUrl = "http://127.0.0.1:', source)
        self.assertIn("$MaxResponseBytes + 1", source)
        self.assertNotIn(".ReadToEnd()", source)
        self.assertIn("$script:CleanupBusyWaitSeconds", source)
        self.assertIn('"operation_may_complete"', source)
        self.assertNotIn("https://", source)

    def test_ci_uses_locked_dependency_resolution(self) -> None:
        source = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("uv sync --locked --all-groups --python 3.12", source)

    def test_update_and_uninstall_keep_user_state_by_default(self) -> None:
        update = (SCRIPTS / "update.ps1").read_text(encoding="utf-8")
        uninstall = (SCRIPTS / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn('Join-Path $PSScriptRoot "install.ps1"', update)
        self.assertIn("$InstallExitCode = $LASTEXITCODE", update)
        self.assertNotIn("ResetConfig", update)
        self.assertIn("if ($RemovePythonEnvironment)", uninstall)
        self.assertIn("if ($RemoveConfig)", uninstall)
        self.assertIn(
            "User images, KRA files, allowed roots and exports were not touched",
            uninstall,
        )

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "requires Windows PowerShell")
    def test_config_swap_preserves_staged_token_and_restores_previous_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config.json"
            stage = root / "transaction" / "config.staged.json"
            transaction = stage.parent
            transaction.mkdir()
            previous = {"token": "preserved-token", "port": 17641}
            updated = {"token": "preserved-token", "port": 17642}
            target.write_text(json.dumps(previous), encoding="utf-8")
            stage.write_text(json.dumps(updated), encoding="utf-8")

            command = (
                f". {_ps_quote(SCRIPTS / 'install-config-helpers.ps1')}; "
                f"$state = New-ConfigSwapState -ConfigPath {_ps_quote(target)} "
                f"-TransactionRoot {_ps_quote(transaction)}; "
                f"Invoke-ConfigSwap -State $state -StageConfig {_ps_quote(stage)}; "
                "$installed = Get-Content -Raw -LiteralPath $state.ConfigPath; "
                "Restore-ConfigSwap -State $state; "
                "$restored = Get-Content -Raw -LiteralPath $state.ConfigPath; "
                "$installedB64 = [Convert]::ToBase64String("
                "[Text.Encoding]::UTF8.GetBytes([string]$installed)); "
                "$restoredB64 = [Convert]::ToBase64String("
                "[Text.Encoding]::UTF8.GetBytes([string]$restored)); "
                "[pscustomobject]@{ installed_b64 = $installedB64; "
                "restored_b64 = $restoredB64; "
                "backup_ready = $state.BackupReady; restored_flag = $state.Restored } "
                "| ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.strip())
            installed = base64.b64decode(payload["installed_b64"]).decode("utf-8")
            restored = base64.b64decode(payload["restored_b64"]).decode("utf-8")
            self.assertEqual(json.loads(installed), updated)
            self.assertEqual(json.loads(restored), previous)
            self.assertTrue(payload["backup_ready"])
            self.assertTrue(payload["restored_flag"])

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "requires Windows PowerShell")
    def test_config_swap_rollback_removes_new_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config.json"
            stage = root / "transaction" / "config.staged.json"
            transaction = stage.parent
            transaction.mkdir()
            stage.write_text('{"token":"new-token"}', encoding="utf-8")

            command = (
                f". {_ps_quote(SCRIPTS / 'install-config-helpers.ps1')}; "
                f"$state = New-ConfigSwapState -ConfigPath {_ps_quote(target)} "
                f"-TransactionRoot {_ps_quote(transaction)}; "
                f"Invoke-ConfigSwap -State $state -StageConfig {_ps_quote(stage)}; "
                "$existed = Test-Path -LiteralPath $state.ConfigPath -PathType Leaf; "
                "Restore-ConfigSwap -State $state; "
                "$removed = -not (Test-Path -LiteralPath $state.ConfigPath); "
                "[pscustomobject]@{ existed = $existed; removed = $removed } "
                "| ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout.strip()),
                {"existed": True, "removed": True},
            )

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "requires Windows PowerShell")
    def test_uncertain_config_rollback_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config.json"
            stage = root / "transaction" / "config.staged.json"
            transaction = stage.parent
            transaction.mkdir()
            target.write_text('{"token":"old-token"}', encoding="utf-8")
            stage.write_text('{"token":"new-token"}', encoding="utf-8")

            command = (
                f". {_ps_quote(SCRIPTS / 'install-config-helpers.ps1')}; "
                f"$state = New-ConfigSwapState -ConfigPath {_ps_quote(target)} "
                f"-TransactionRoot {_ps_quote(transaction)}; "
                f"Invoke-ConfigSwap -State $state -StageConfig {_ps_quote(stage)}; "
                "Remove-Item -LiteralPath $state.ConfigPath -Force; "
                "New-Item -ItemType Directory -Path $state.ConfigPath | Out-Null; "
                "$failed = $false; try { Restore-ConfigSwap -State $state } "
                "catch { $failed = $true }; "
                "[pscustomobject]@{ failed = $failed; "
                "backup_retained = (Test-Path -LiteralPath $state.BackupPath -PathType Leaf) } "
                "| ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout.strip()),
                {"failed": True, "backup_retained": True},
            )


if __name__ == "__main__":
    unittest.main()
