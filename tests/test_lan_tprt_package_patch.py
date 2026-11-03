from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import unittest
import zipfile
import zlib

from external_assets import external_path, require_file

ROOT = Path(__file__).resolve().parents[1]
APK = external_path("LJJC_ORIGINAL_APK", "runtime/installed-base.apk")
SPEC = importlib.util.spec_from_file_location("lan_tprt_package", ROOT / "client-patch" / "lan" / "patch_tprt_package.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class TprtPackagePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_file(APK, "LJJC_ORIGINAL_APK")
        with zipfile.ZipFile(APK) as archive:
            cls.libs = {abi: archive.read(f"lib/{abi}/libtprt.so") for abi in patch.PATCHES}

    def test_package_is_retargeted_and_crc_is_preserved(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, report = patch.patch_library(original, abi)
                self.assertEqual(hashlib.sha256(original).hexdigest(), patch.PATCHES[abi]["sha256"])
                self.assertEqual(zlib.crc32(output) & 0xFFFFFFFF, patch.PATCHES[abi]["crc32"])
                offset = patch.PATCHES[abi]["packageOffset"]
                self.assertEqual(output[offset:offset + len(patch.ORIGINAL_PACKAGE)],
                                 patch.TARGET_PACKAGE + b"\0" * (len(patch.ORIGINAL_PACKAGE) - len(patch.TARGET_PACKAGE)))
                allowed = set(range(offset, offset + len(patch.ORIGINAL_PACKAGE))) | set(
                    range(patch.PATCHES[abi]["crcFixOffset"], patch.PATCHES[abi]["crcFixOffset"] + 4))
                self.assertTrue(set(i for i, (a, b) in enumerate(zip(original, output)) if a != b) <= allowed)
                self.assertEqual(len(output), len(original))

    def test_digest_and_padding_guards_fail_closed(self):
        for abi, original in self.libs.items():
            with self.subTest(abi=abi):
                output, _ = patch.patch_library(original, abi)
                with self.assertRaises(ValueError):
                    patch.patch_library(output, abi)
                corrupted = bytearray(original)
                corrupted[patch.PATCHES[abi]["crcFixOffset"]] = 1
                with self.assertRaises(ValueError):
                    patch.patch_library(bytes(corrupted), abi)


if __name__ == "__main__":
    unittest.main()
