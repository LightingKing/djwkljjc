from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.inspect_il2cpp import MAGIC, V23_TABLES, inspect


class Il2CppInspectionTests(unittest.TestCase):
    def test_valid_v23_header_and_elf_probe_are_reported_without_dump_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "global-metadata.dat"
            header = bytearray()
            header += MAGIC.to_bytes(4, "little")
            header += (23).to_bytes(4, "little")
            header += (8 + len(V23_TABLES) * 8).to_bytes(4, "little")
            header += (4).to_bytes(4, "little")
            for _ in V23_TABLES[1:]:
                header += (8 + len(V23_TABLES) * 8).to_bytes(4, "little")
                header += (0).to_bytes(4, "little")
            metadata.write_bytes(header + b"\x00\x00\x00\x00")
            binary = root / "libil2cpp.so"
            binary.write_bytes(b"\x7fELF" + bytes([1, 1]) + b"\x00" * 12 + (40).to_bytes(2, "little") + b"il2cpp_init")
            report = inspect(metadata, [binary])
            self.assertTrue(report["metadata"]["isValid"])
            self.assertEqual(report["metadata"]["version"], 23)
            self.assertEqual(report["binaries"][0]["machineName"], "ARM")
            self.assertFalse(report["tooling"]["dumpCsGenerated"])

    def test_records_versioned_dumper_and_incomplete_ghidra_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "input" / "global-metadata.dat"
            metadata.parent.mkdir()
            header = bytearray()
            header += MAGIC.to_bytes(4, "little")
            header += (23).to_bytes(4, "little")
            header += (8 + len(V23_TABLES) * 8).to_bytes(4, "little")
            header += (4).to_bytes(4, "little")
            for _ in V23_TABLES[1:]:
                header += (8 + len(V23_TABLES) * 8).to_bytes(4, "little")
                header += (0).to_bytes(4, "little")
            metadata.write_bytes(header + b"\x00\x00\x00\x00")
            binary = root / "input" / "lib" / "x86" / "libil2cpp.so"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\x7fELF" + bytes([1, 1]) + b"\x00" * 12 + (3).to_bytes(2, "little") + b"il2cpp_init")

            (root / "runtime" / "il2cppdumper-v6.7.46" / "app").mkdir(parents=True)
            (root / "runtime" / "il2cppdumper-v6.7.46" / "Il2CppDumper-win-v6.7.46.zip").write_bytes(b"zip")
            (root / "runtime" / "il2cppdumper-v6.7.46" / "app" / "Il2CppDumper-x86.exe").write_bytes(b"exe")
            dump_root = root / "analysis" / "il2cpp" / "dump-v6.7.46-x86"
            dump_root.mkdir(parents=True)
            for name in ("dump.cs", "il2cpp.h", "script.json", "stringliteral.json"):
                (dump_root / name).write_text(name, encoding="utf-8")
            ghidra_root = root / "analysis" / "il2cpp" / "ghidra-project-x86"
            ghidra_root.mkdir(parents=True)
            (ghidra_root / "LJJC_IL2CPP_X86.gpr").write_text("", encoding="utf-8")
            (root / "analysis" / "il2cpp" / "ghidra-x86.log").write_text(
                "IMPORTING libil2cpp.so\nImport succeeded\nAnalysis timed out at 300 seconds\n", encoding="utf-8"
            )
            output = root / "analysis" / "il2cpp" / "structure-audit.json"
            report = inspect(metadata, [binary], repo_root=root, output_path=output)
            tooling = report["tooling"]
            self.assertTrue(tooling["il2cppDumper"]["dumpCsGenerated"])
            self.assertEqual(tooling["il2cppDumper"]["version"], "6.7.46")
            self.assertEqual(tooling["ghidra"]["status"], "imported_analysis_timeout")
            self.assertTrue(tooling["ghidra"]["imported"])
            self.assertFalse(tooling["ghidra"]["analysisCompleted"])


if __name__ == "__main__":
    unittest.main()

