"""Evaluate PowerShell launch planning without services, live files or commits."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/start-committed-release.ps1"
PWSH = shutil.which("pwsh")
PYTHON = Path("C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe")


@unittest.skipUnless(os.name == "nt" and PWSH and PYTHON.is_file(), "Windows release launcher")
class ReleaseLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release launch ")
        self.root = Path(self.temp.name)
        self.code = self.root / "clean checkout"
        config = self.code / "tools/config"
        config.mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/config/release-loopback.toml", config / "release-loopback.toml")
        (self.code / "server.py").write_text("raise RuntimeError('server must never run in launch-plan tests')", encoding="utf-8")
        # A repo's ordinary config must never redirect this release to a new
        # database simply because explicit --config support was added.
        (self.code / "server.toml").write_text('[server]\nunified_store_path="wrong.sqlite3"\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, config=None):
        env = dict(os.environ, LJJC_TEST_LAUNCH_SCRIPT=str(SCRIPT), LJJC_TEST_CODE=str(self.code))
        if config:
            env["LJJC_TEST_CONFIG"] = str(config)
        # Stub only Git metadata; no test commits are made. The actual release
        # script, TOML parser, validation, arguments and JSON serialization run.
        command = r'''
function git {
    $global:LASTEXITCODE = 0
    if ($args -contains 'rev-parse') { 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
    elseif ($args -contains 'status') { }
    else { throw 'Unexpected Git operation in launch planning' }
}
function Start-Process { throw 'PlanOnly must not start a process' }
function Get-NetTCPConnection { throw 'PlanOnly must not inspect runtime listeners' }
$launch = @{CodeDirectory=$env:LJJC_TEST_CODE; PlanOnly=$true}
if ($env:LJJC_TEST_CONFIG) { $launch.ConfigFile=$env:LJJC_TEST_CONFIG }
& $env:LJJC_TEST_LAUNCH_SCRIPT @launch
'''
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
                                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")), before)
        return result

    def external(self, text):
        path = self.root / "external LAN config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    @unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell 5.1 is not installed")
    def test_windows_powershell_rejected_before_any_release_work(self):
        result = subprocess.run([
            shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
            "-CodeDirectory", str(self.root / "does-not-exist"), "-PlanOnly",
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PowerShell 7 (pwsh) is required", result.stderr)
        self.assertFalse((self.root / "does-not-exist").exists())

    def assert_state_is_original(self, plan):
        args = plan["arguments"]
        self.assertEqual(args[args.index("--state-dir") + 1], "I:/workspace/mobilegame/analysis/ljjc-local/state")
        self.assertEqual(args[args.index("--unified-store-path") + 1], "I:/workspace/mobilegame/analysis/ljjc-local/state/local.sqlite3")
        self.assertEqual(args[args.index("--http-port") + 1], "19080")
        self.assertEqual(args[args.index("--tls-port") + 1], "19102")

    def test_default_explicit_config_preserves_preexisting_launch_semantics(self):
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        expected = self.code / "tools/config/release-loopback.toml"
        self.assertEqual(Path(plan["configFile"]), expected)
        self.assertEqual(Path(plan["arguments"][plan["arguments"].index("--config") + 1]), expected)
        self.assertEqual(plan["configSha256"], hashlib.sha256(expected.read_bytes()).hexdigest())
        self.assertEqual(plan["network"]["bind_host"], "127.0.0.1")
        self.assertEqual(plan["network"]["public_base_url"], "http://127.0.0.1:19080")
        self.assertTrue(plan["network"]["legacy_tls"])
        self.assertIn("--legacy-tls", plan["arguments"])
        self.assert_state_is_original(plan)

    def test_explicit_lan_configuration_is_passed_and_hashed(self):
        config = self.external((ROOT / "tools/config/release-lan.example.toml").read_text(encoding="utf-8"))
        result = self.plan(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(Path(plan["arguments"][plan["arguments"].index("--config") + 1]), config)
        self.assertEqual(plan["configSha256"], hashlib.sha256(config.read_bytes()).hexdigest())
        self.assertEqual(plan["network"]["bind_host"], "0.0.0.0")
        self.assertEqual(plan["network"]["public_base_url"], "http://192.168.1.22:19080")
        self.assert_state_is_original(plan)

    def test_disabled_legacy_tls_is_not_overridden_by_launch_arguments(self):
        config = self.external('[server]\nlegacy_tls=false\n')
        result = self.plan(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertFalse(plan["network"]["legacy_tls"])
        self.assertNotIn("--legacy-tls", plan["arguments"])
        self.assertEqual(Path(plan["arguments"][plan["arguments"].index("--config") + 1]), config)
        self.assert_state_is_original(plan)

    def test_storage_clock_and_isolated_port_configurations_cannot_alter_production(self):
        for line in ('unified_store_path="other.sqlite3"', 'time_offset_seconds=86400',
                     'backup_dir="other"', 'http_port=19082', 'enable_tls=false'):
            with self.subTest(setting=line):
                result = self.plan(self.external("[server]\n" + line + "\n"))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("configuration validation failed", result.stderr)

    def test_public_and_wildcard_advertised_urls_are_rejected(self):
        for origin in ("http://8.8.8.8:19080", "http://0.0.0.0:19080", "http://192.168.1.22:19080/path"):
            with self.subTest(origin=origin):
                result = self.plan(self.external(f'[server]\npublic_base_url="{origin}"\n'))
                self.assertNotEqual(result.returncode, 0)

    def test_isolated_example_has_separate_port_database_and_no_tls(self):
        config = tomllib.loads((ROOT / "tools/config/lan-isolated-19082.example.toml").read_text(encoding="utf-8"))["server"]
        self.assertEqual(config["http_port"], 19082)
        self.assertEqual(config["public_base_url"], "http://192.168.1.22:19082")
        self.assertFalse(config["enable_tls"])
        self.assertEqual(Path(config["unified_store_path"]), Path("I:/workspace/mobilegame/runtime/lan-validation-19082/state/local.sqlite3"))
