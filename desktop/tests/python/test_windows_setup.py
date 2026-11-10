"""Exercise the real setup version gate without installing or replacing anything."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from toolbox_platform import windows_powershell_executable


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(os.name == "nt", "Windows initialization")
class WindowsSetupTests(unittest.TestCase):
    def check_version(self, expected):
        with tempfile.TemporaryDirectory(prefix="初始化 版本 ") as directory:
            script = Path(directory) / "check.ps1"
            script.write_text(
                """param([string]$Expected, [string]$Python, [string]$Setup)
$ErrorActionPreference = 'Stop'
$expectedPython = $Expected
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Setup, [ref]$null, [ref]$null)
$gate = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-PinnedPython'
}, $true)
if (-not $gate) { throw 'Setup version gate missing' }
Invoke-Expression $gate.Extent.Text
Assert-PinnedPython $Python
Write-Output 'PINNED_PYTHON=OK'
""", encoding="utf-8-sig",
            )
            return subprocess.run(
                [str(windows_powershell_executable()), "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(script), expected,
                 sys.executable, str(ROOT / "setup_windows.ps1")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            )

    def test_pinned_interpreter_passes(self):
        expected = (ROOT / ".python-version").read_text().strip()
        result = self.check_version(expected)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PINNED_PYTHON=OK", result.stdout)

    def test_mismatched_patch_version_is_rejected(self):
        result = self.check_version("3.11.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PINNED_PYTHON=OK", result.stdout)
        self.assertIn("required by .python-version", result.stderr)
