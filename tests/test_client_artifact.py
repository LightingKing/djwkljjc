from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ljjc_client_artifact", ROOT / "dev" / "verify-client-artifact.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ClientArtifactVerificationTests(unittest.TestCase):
    def test_parse_badging_extracts_package_version_and_target(self) -> None:
        output = (
            "package: name='com.tencent.tmgp.fcicv' versionCode='59162' "
            "versionName='2.0.4' platformBuildVersionName='14'\n"
            "sdkVersion:'22' targetSdkVersion:'24'\n"
        )
        self.assertEqual(
            MODULE.parse_badging(output),
            {
                "package": "com.tencent.tmgp.fcicv",
                "versionCode": "59162",
                "versionName": "2.0.4",
                "targetSdk": "24",
            },
        )

    def test_parse_apksigner_requires_verified_line_and_records_schemes(self) -> None:
        output = """Verified\nVerified using v1 scheme: true\nVerified using v2 scheme: true\nVerified using v3 scheme: true\nSigner #1 certificate DN: CN=local\n"""
        parsed = MODULE.parse_apksigner(output)
        self.assertTrue(parsed["verified"])
        self.assertEqual(parsed["schemes"], {"v1": True, "v2": True, "v3": True})
        self.assertEqual(parsed["signerCount"], 1)

    def test_parse_installed_apk_path_and_digest(self) -> None:
        self.assertEqual(
            MODULE._installed_apk_path("package:/data/app/example/base.apk\n"),
            "/data/app/example/base.apk",
        )
        digest = "A" * 64
        self.assertEqual(MODULE._sha256sum(f"{digest}  /data/app/example/base.apk\n"), digest.lower())
        self.assertIsNone(MODULE._sha256sum("sha256sum: inaccessible"))

    def test_sha256_mismatch_stops_before_external_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "artifact.apk"
            apk.write_bytes(b"safe test artifact")
            report = MODULE.verify_artifact(
                apk=apk,
                expected_sha256="0" * 64,
                apksigner=Path(temp) / "missing-apksigner",
                aapt2=Path(temp) / "missing-aapt2",
            )
            self.assertFalse(report["passed"])
            self.assertEqual(report["error"], "SHA-256 mismatch")
            self.assertFalse(report["signature"]["verified"])

    def test_missing_tool_evidence_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "artifact.apk"
            apk.write_bytes(b"safe test artifact")
            report = MODULE.verify_artifact(
                apk=apk,
                expected_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
                apksigner=Path(temp) / "missing-apksigner",
                aapt2=Path(temp) / "missing-aapt2",
            )
            self.assertFalse(report["passed"])
            self.assertEqual(report["signature"]["error"], "apksigner not found")
            self.assertEqual(report["metadata"]["error"], "aapt2/aapt not found")


if __name__ == "__main__":
    unittest.main()
