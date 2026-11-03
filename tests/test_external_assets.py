import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from external_assets import external_path, require_file


class ExternalAssetPathTests(unittest.TestCase):
    def test_configured_root_and_individual_override_are_checkout_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"LJJC_TEST_ASSET_ROOT": str(root)}, clear=True):
                self.assertEqual(external_path("LJJC_ORIGINAL_APK", "runtime/installed-base.apk"),
                                 root / "runtime/installed-base.apk")
                explicit = root / "separate client.apk"
                os.environ["LJJC_ORIGINAL_APK"] = str(explicit)
                self.assertEqual(external_path("LJJC_ORIGINAL_APK", "runtime/installed-base.apk"), explicit)

    def test_unavailable_fixture_skips_with_configuration_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.apk"
            with self.assertRaisesRegex(unittest.SkipTest, "set LJJC_ORIGINAL_APK or LJJC_TEST_ASSET_ROOT"):
                require_file(path, "LJJC_ORIGINAL_APK")
            path.write_bytes(b"test fixture")
            self.assertEqual(require_file(path, "LJJC_ORIGINAL_APK"), path)
