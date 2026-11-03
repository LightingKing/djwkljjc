from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

from external_assets import external_path, require_file

ROOT = Path(__file__).resolve().parents[1]
APK = external_path("LJJC_ORIGINAL_APK", "runtime/installed-base.apk")
SPEC = importlib.util.spec_from_file_location("lan_sdk_bridge", ROOT / "client-patch" / "lan" / "patch_sdk_bridge.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class SetBridgePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_file(APK, "LJJC_ORIGINAL_APK")
        with zipfile.ZipFile(APK) as archive:
            cls.libs = {abi: archive.read(f"lib/{abi}/libMsdkAdapter.so") for abi in patch.PATCHES}

    def test_only_set_bridge_entry_bytes_change(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, report = patch.patch_library(original, abi)
                spec = patch.PATCHES[abi]
                changed = [i for i, (a, b) in enumerate(zip(original, output)) if a != b]
                self.assertEqual(changed, list(range(spec["offset"], spec["offset"] + len(spec["return"]))))
                self.assertEqual(output[spec["offset"]:spec["offset"] + len(spec["return"])], spec["return"])
                self.assertEqual(len(output), len(original))
                self.assertEqual(hashlib.sha256(output).hexdigest(), report["sha256"])
                self.assertEqual(report["symbol"], "setBridge")
                self.assertEqual(report["signature"], "void setBridge(delegate)")
                self.assertTrue(report["otherBytesUnchanged"])

    def test_original_function_guard_is_strict_and_idempotence_is_rejected(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, _ = patch.patch_library(original, abi)
                with self.assertRaises(ValueError):
                    patch.patch_library(output, abi)
                corrupted = bytearray(original)
                corrupted[patch.PATCHES[abi]["offset"] + 1] ^= 1
                with self.assertRaises(ValueError):
                    patch.patch_library(bytes(corrupted), abi)
        with self.assertRaises(ValueError):
            patch.patch_library(b"unverified", "x86")

    def test_cli_emits_only_replacement_libraries_without_touching_source(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, output, report = folder / "source.apk", folder / "out", folder / "report.json"
            with zipfile.ZipFile(source, "w") as archive:
                for abi, payload in self.libs.items():
                    archive.writestr(f"lib/{abi}/libMsdkAdapter.so", payload)
            before = source.read_bytes()
            self.assertEqual(patch.main(["--apk", str(source), "--output-dir", str(output), "--report", str(report)]), 0)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(sorted(p.relative_to(output).as_posix() for p in output.rglob("*.so")),
                             ["lib/armeabi-v7a/libMsdkAdapter.so", "lib/x86/libMsdkAdapter.so"])


if __name__ == "__main__":
    unittest.main()
