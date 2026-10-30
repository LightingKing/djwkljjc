from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ToolchainBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.script = cls.root / "tools" / "bootstrap-toolchain.ps1"
        cls.powershell = shutil.which("powershell") or shutil.which("pwsh")

    def _run(self, *extra: str):
        if not self.powershell:
            self.skipTest("PowerShell is not installed")
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            report = temp_root / "report.json"
            command = [
                self.powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
                "-SdkRoot",
                str(temp_root / "missing-sdk"),
                "-JavaHome",
                str(temp_root / "missing-java"),
                "-Python",
                sys.executable,
                "-Requirements",
                str(self.root / "requirements.txt"),
                "-Output",
                str(report),
                *extra,
            ]
            result = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=os.environ.copy(),
                timeout=30,
            )
            self.assertTrue(report.exists(), result.stdout + result.stderr)
            return result, json.loads(report.read_text(encoding="utf-8-sig"))

    def test_default_mode_is_read_only_and_reports_install_suggestions(self):
        result, report = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["status"], "needs_install")
        self.assertIn("Android SDK root", report["missing"])
        self.assertTrue(report["installCommands"])
        self.assertNotIn("Install mode enabled", result.stdout)

    def test_strict_mode_reports_missing_without_running_installers(self):
        result, report = self._run("-Strict")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["status"], "needs_install")
        self.assertNotIn("Install mode enabled", result.stdout)


if __name__ == "__main__":
    unittest.main()
