from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from external_assets import external_path, require_file

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_APK = external_path("LJJC_ORIGINAL_APK", "runtime/installed-base.apk")
SPEC = importlib.util.spec_from_file_location(
    "lan_sdk_log_patch", ROOT / "client-patch" / "lan" / "patch_sdk_log.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)

try:
    import capstone
    from elftools.elf.elffile import ELFFile
except ImportError:
    capstone = ELFFile = None


class SdkLogPatchInputTests(unittest.TestCase):
    def test_unrecognized_library_or_abi_is_rejected(self):
        for abi in ("x86", "armeabi-v7a", "arm64-v8a", "unknown"):
            with self.subTest(abi=abi), self.assertRaises(ValueError):
                patch.patch_library(b"unverified ELF", abi)


@unittest.skipUnless(ELFFile and capstone,
                     "pyelftools and capstone are needed for binary integration checks")
class SdkLogPatchBinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_file(ORIGINAL_APK, "LJJC_ORIGINAL_APK")
        with zipfile.ZipFile(ORIGINAL_APK) as archive:
            cls.libraries = {abi: archive.read(f"lib/{abi}/libMsdkAdapter.so") for abi in patch.PATCHES}

    def test_exact_export_returns_before_touching_caller_stack(self):
        for abi, original in self.libraries.items():
            with self.subTest(abi=abi):
                output, report = patch.patch_library(original, abi)
                elf = ELFFile(io.BytesIO(original))
                symbol = next(s for s in elf.get_section_by_name(".dynsym").iter_symbols()
                              if s.name == "BuglyLog")
                self.assertEqual(symbol.entry.st_value, report["symbolValue"])
                address = symbol.entry.st_value & ~1 if abi == "armeabi-v7a" else symbol.entry.st_value
                segment = next(s for s in elf.iter_segments() if s.header.p_type == "PT_LOAD"
                               and s.header.p_vaddr <= address < s.header.p_vaddr + s.header.p_filesz)
                offset = address - segment.header.p_vaddr + segment.header.p_offset
                self.assertEqual(offset, report["fileOffset"])
                self.assertTrue(segment.header.p_flags & 1)  # Executable segment.
                if abi == "x86":
                    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
                    expected = ("ret", "")
                    changed = [offset]
                else:
                    self.assertTrue(symbol.entry.st_value & 1)  # Thumb entry.
                    decoder = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
                    expected = ("bx", "lr")
                    changed = [offset, offset + 1]
                first = next(decoder.disasm(output[offset:offset + 8], address))
                self.assertEqual((first.mnemonic, first.op_str), expected)
                self.assertEqual(len(output), len(original))
                # Proves Unity/Android console code, SDK initialization and all
                # other exports are byte-identical, not merely still present.
                self.assertEqual([i for i, (a, b) in enumerate(zip(original, output)) if a != b], changed)
                self.assertEqual(hashlib.sha256(output).hexdigest(), report["sha256"])

    def test_cli_only_emits_two_replacement_libraries_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            apk, output, report = folder / "source.apk", folder / "out", folder / "report.json"
            with zipfile.ZipFile(apk, "w") as archive:
                for abi, payload in self.libraries.items():
                    archive.writestr(f"lib/{abi}/libMsdkAdapter.so", payload)
                archive.writestr("lib/x86/libtprt.so", b"untouched dependency")
            before = apk.read_bytes()
            self.assertEqual(patch.main(["--apk", str(apk), "--output-dir", str(output),
                                         "--report", str(report)]), 0)
            self.assertEqual(apk.read_bytes(), before)
            self.assertEqual(sorted(p.relative_to(output).as_posix() for p in output.rglob("*.so")),
                             ["lib/armeabi-v7a/libMsdkAdapter.so", "lib/x86/libMsdkAdapter.so"])
            with self.assertRaises(SystemExit):
                patch.main(["--apk", str(apk), "--output-dir", str(output), "--report", str(apk)])
            self.assertEqual(apk.read_bytes(), before)

    def test_reapplying_to_a_modified_library_fails_closed(self):
        for abi, original in self.libraries.items():
            with self.subTest(abi=abi):
                output, _ = patch.patch_library(original, abi)
                with self.assertRaises(ValueError):
                    patch.patch_library(output, abi)


if __name__ == "__main__":
    unittest.main()
