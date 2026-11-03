from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APK = ROOT.parents[1] / "runtime" / "installed-base.apk"
SPEC = importlib.util.spec_from_file_location("lan_sdk_noop_patch", ROOT / "client-patch" / "lan" / "patch_sdk_noop.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class SetPermissionPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(APK) as z:
            cls.libs = {abi: z.read(f"lib/{abi}/libMsdkAdapter.so") for abi in patch.PATCHES}

    def test_exact_entry_is_replaced_and_only_entry_bytes_change(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, report = patch.patch_library(original, abi)
                spec = patch.PATCHES[abi]
                self.assertEqual(report["symbol"], "SetPermission")
                self.assertEqual(hashlib.sha256(output).hexdigest(), report["sha256"])
                changed = [i for i, (a, b) in enumerate(zip(original, output)) if a != b]
                self.assertEqual(changed, list(range(spec["offset"], spec["offset"] + len(spec["return"]))))
                self.assertEqual(output[spec["offset"]:spec["offset"] + len(spec["return"])], spec["return"])
                self.assertEqual(len(output), len(original))
                self.assertTrue(report["otherBytesUnchanged"])

    def test_source_digest_and_exact_function_guard_fail_closed(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, _ = patch.patch_library(original, abi)
                with self.assertRaises(ValueError):
                    patch.patch_library(output, abi)
                corrupted = bytearray(original)
                corrupted[patch.PATCHES[abi]["offset"] + 3] ^= 1
                with self.assertRaises(ValueError):
                    patch.patch_library(bytes(corrupted), abi)
        with self.assertRaises(ValueError):
            patch.patch_library(b"not an ELF", "x86")

    def test_cli_does_not_modify_source_or_emit_other_abis(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, out, report = folder / "source.apk", folder / "out", folder / "report.json"
            with zipfile.ZipFile(source, "w") as z:
                for abi, payload in self.libs.items():
                    z.writestr(f"lib/{abi}/libMsdkAdapter.so", payload)
            before = source.read_bytes()
            self.assertEqual(patch.main(["--apk", str(source), "--output-dir", str(out), "--report", str(report)]), 0)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*.so")),
                             ["lib/armeabi-v7a/libMsdkAdapter.so", "lib/x86/libMsdkAdapter.so"])
            self.assertEqual(len(__import__("json").loads(report.read_text())["libraries"]), 2)


if __name__ == "__main__":
    unittest.main()
