from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


class InstallScriptContractTests(unittest.TestCase):
    def test_powershell_scripts_are_thin_python_cli_wrappers(self) -> None:
        install = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        update = (SCRIPTS / "update.ps1").read_text(encoding="utf-8")
        check = (SCRIPTS / "check-installation.ps1").read_text(encoding="utf-8")
        uninstall = (SCRIPTS / "uninstall.ps1").read_text(encoding="utf-8")
        for source in (install, update, check, uninstall):
            self.assertIn("krita-codex-install.exe", source)
            self.assertNotIn("krita_codex_protocol", source)
            self.assertNotIn("$StagePlugin", source)
            self.assertNotIn("Invoke-ConfigSwap", source)
        self.assertIn('$CliArguments.Add("--allowed-root")', install)
        self.assertIn("& $Installer update", update)
        self.assertIn('$Arguments.Add("--offline")', check)
        self.assertIn('$Arguments.Add("--remove-config")', uninstall)

    def test_python_package_owns_installation_behavior(self) -> None:
        manager = (
            REPO_ROOT / "src" / "krita_codex_mcp" / "installer" / "manager.py"
        ).read_text(encoding="utf-8")
        preflight = (
            REPO_ROOT / "src" / "krita_codex_mcp" / "installer" / "preflight.py"
        ).read_text(encoding="utf-8")
        port_reservation = (
            REPO_ROOT
            / "src"
            / "krita_codex_mcp"
            / "installer"
            / "port_reservation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class InstallationManager", manager)
        self.assertIn('self._fault(f"after_swap_{component.name}")', manager)
        self.assertIn("previous installation was restored", manager)
        self.assertIn("BridgePortReservation", manager)
        self.assertIn("SO_EXCLUSIVEADDRUSE", port_reservation)
        self.assertIn("close Krita or free the", port_reservation)
        self.assertIn('"preview",', preflight)
        self.assertIn('"save_export",', preflight)
        self.assertIn('"compatibility"', preflight)
        self.assertIn('"NATIVE_DRAW_PROBE_FAILED"', preflight)
        self.assertIn('"LAYER_WORKFLOW_PROBE_FAILED"', preflight)
        self.assertIn("_validate_runtime_versions", preflight)

    def test_ci_uses_locked_dependency_resolution(self) -> None:
        source = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("uv sync --locked --all-groups --python 3.12", source)

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "requires Windows PowerShell")
    def test_powershell_scripts_parse(self) -> None:
        for path in sorted(SCRIPTS.glob("*.ps1")):
            command = (
                "$errors = @(); "
                f"[System.Management.Automation.Language.Parser]::ParseFile('{path}', "
                "[ref]$null, [ref]$errors) | Out-Null; "
                "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
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
            self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
