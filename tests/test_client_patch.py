from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ljjc_client_patch", ROOT / "client-patch" / "build.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ClientPatchPipelineTests(unittest.TestCase):
    def test_repository_dry_run_manifest_tracks_real_apk_digest(self) -> None:
        manifest_path = ROOT / "client-patch" / "patches" / "ljjc-offline-2.0.4-dryrun.json"
        manifest = MODULE.load_patch_plan(manifest_path)
        self.assertEqual(manifest.version, "2.0.4")
        self.assertEqual(manifest.expected_sha256, "b997dbac9ff1764b46809486d83e4a65c6d511cae17fee6f8063ef598d66a776")
        self.assertEqual(manifest.operations, ())

    def test_repository_sdk_shim_manifest_uses_safe_replace_file_paths(self) -> None:
        manifest_path = ROOT / "client-patch" / "patches" / "ljjc-offline-sdkshim-2.0.4.json"
        manifest = MODULE.load_patch_plan(manifest_path)
        self.assertEqual(len(manifest.operations), 1)
        operation = manifest.operations[0]
        self.assertEqual(operation["kind"], "replace_file")
        self.assertEqual(operation["target"], "smali/com/actgames/fci/AppDelegater.smali")
        self.assertEqual(operation["source"], "offline-smali/com/actgames/fci/AppDelegater.smali")

    def test_target24_manifest_is_explicit_and_only_changes_sdk_metadata(self) -> None:
        manifest_path = ROOT / "client-patch" / "patches" / "ljjc-offline-target24-2.0.4.json"
        manifest = MODULE.load_patch_plan(manifest_path)
        self.assertEqual(manifest.version, "2.0.4")
        self.assertEqual(len(manifest.operations), 1)
        operation = manifest.operations[0]
        self.assertEqual(operation["kind"], "replace_text")
        self.assertEqual(operation["target"], "apktool.yml")
        self.assertEqual(operation["find"], "targetSdkVersion: 22")
        self.assertEqual(operation["replace"], "targetSdkVersion: 24")
        self.assertEqual(operation["count"], 1)

    def test_dry_run_verifies_digest_and_never_changes_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "original.apk"
            source.write_bytes(b"source APK bytes\x00")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "patch.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1,
                "name": "test",
                "version": "1",
                "abi": "x86",
                "expectedSha256": digest,
                "operations": [],
            }), encoding="utf-8")
            before = source.read_bytes()
            report = MODULE.build_apk(input_apk=source, output_dir=root / "out", manifest=manifest, dry_run=True)
            self.assertEqual(report["status"], "dry-run")
            self.assertEqual(report["inputSha256"], digest)
            self.assertEqual(source.read_bytes(), before)
            self.assertTrue(Path(report["report"]).is_file())
            self.assertFalse((root / "out" / "test-1-x86.apk").exists())

    def test_digest_mismatch_is_rejected_before_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "original.apk"
            source.write_bytes(b"source APK bytes")
            manifest = root / "patch.json"
            manifest.write_text(json.dumps({
                "name": "test", "version": "1", "abi": "x86",
                "expectedSha256": "1" * 64, "operations": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PipelineError, "SHA-256 mismatch"):
                MODULE.build_apk(input_apk=source, output_dir=root / "out", manifest=manifest, dry_run=True)

    def test_missing_tools_have_explicit_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "original.apk"
            source.write_bytes(b"source APK bytes")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "patch.json"
            manifest.write_text(json.dumps({
                "name": "test", "version": "1", "abi": "x86",
                "expectedSha256": digest, "operations": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PipelineError, "required APK tools are missing"):
                MODULE.build_apk(
                    input_apk=source, output_dir=root / "out", manifest=manifest,
                    tools={"apktool": root / "missing-apktool", "zipalign": root / "missing-zipalign", "apksigner": root / "missing-apksigner"},
                    keystore=root / "missing-keystore", key_alias="test",
                )

    def test_text_patch_requires_exact_match_and_stays_in_decoded_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            decoded = root / "decoded"
            patch_root = root / "patches"
            decoded.mkdir()
            patch_root.mkdir()
            target = decoded / "AndroidManifest.xml"
            target.write_text("old\n", encoding="utf-8")
            plan = MODULE.PatchPlan("test", "1", "x86", "0" * 64, ({
                "kind": "replace_text", "target": "AndroidManifest.xml",
                "find": "old", "replace": "new", "count": 1,
            },))
            MODULE.apply_operations(plan, decoded_root=decoded, patch_root=patch_root)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            bad = MODULE.PatchPlan("test", "1", "x86", "0" * 64, ({
                "kind": "replace_text", "target": "../outside",
                "find": "old", "replace": "new", "count": 1,
            },))
            with self.assertRaises(MODULE.PipelineError):
                MODULE.apply_operations(bad, decoded_root=decoded, patch_root=patch_root)


if __name__ == "__main__":
    unittest.main()
