from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APK = ROOT.parents[1] / "runtime" / "installed-base.apk"
SPEC = importlib.util.spec_from_file_location("lan_sdk_report", ROOT / "client-patch" / "lan" / "patch_sdk_report.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class ReportUnityDataPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(APK) as archive:
            cls.libs = {abi: archive.read(f"lib/{abi}/libMsdkAdapter.so") for abi in patch.PATCHES}

    def test_complete_audited_function_hash_and_only_entry_changes(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, report = patch.patch_library(original, abi)
                spec = patch.PATCHES[abi]
                function = original[spec["offset"]:spec["offset"] + spec["size"]]
                self.assertEqual(hashlib.sha256(function).hexdigest(), spec["functionSha256"])
                self.assertEqual(len(function), spec["size"])
                changed = [i for i, (a, b) in enumerate(zip(original, output)) if a != b]
                self.assertEqual(changed, list(range(spec["offset"], spec["offset"] + len(spec["return"]))))
                self.assertEqual(output[spec["offset"]:spec["offset"] + len(spec["return"])], spec["return"])
                self.assertEqual(len(output), len(original))
                self.assertEqual(hashlib.sha256(output).hexdigest(), report["sha256"])
                self.assertEqual(report["symbol"], "ReportUnityData")
                self.assertTrue(report["otherBytesUnchanged"])

    def test_whole_file_and_complete_function_guards_fail_closed(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, _ = patch.patch_library(original, abi)
                with self.assertRaises(ValueError):
                    patch.patch_library(output, abi)
                corrupted = bytearray(original)
                corrupted[patch.PATCHES[abi]["offset"] + 32] ^= 1
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
