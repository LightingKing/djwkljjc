from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lan_bootstrap_patch", ROOT / "client-patch" / "lan" / "patch_bootstrap.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)
FIXTURE = ROOT / "tests" / "fixtures" / "lan-gameconstconfig.bundle"


def config_message(data):
    # Independent protobuf parser for the actual config.proto fields recovered
    # from the original client's descriptor. Unknown fields remain preserved.
    descriptor = descriptor_pb2.FileDescriptorProto(name="bootstrap_test.proto", syntax="proto3")
    message = descriptor.message_type.add(name="GameConstConfig")
    for number, name, kind in [(1, "appId", 9), (2, "appKey", 9),
                               (4, "localedConfigUrl", 9), (5, "gameId", 5),
                               (6, "channelId", 9), (9, "purchasePlatform", 5),
                               (10, "defaultLanguage", 9)]:
        message.field.add(name=name, number=number, type=kind, label=3 if number == 4 else 1)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(descriptor)
    result = message_factory.GetMessageClass(pool.FindMessageTypeByName("GameConstConfig"))()
    result.ParseFromString(data)
    return result


def plaintext(bundle):
    _, _, tree = patch.text_asset(bundle)
    return patch.decode_config(tree["m_Script"].encode("utf-8", "surrogateescape"))[0]


class LanBootstrapPatchTests(unittest.TestCase):
    def test_original_apk_fixture_and_config_identity(self):
        raw = FIXTURE.read_bytes()
        self.assertEqual(patch.hashlib.sha256(raw).hexdigest(), patch.ORIGINAL_SHA256)
        config = config_message(plaintext(raw))
        self.assertEqual(list(config.localedConfigUrl), [patch.ORIGINAL_URL])
        self.assertEqual((config.appId, config.gameId, config.channelId,
                          config.purchasePlatform, config.defaultLanguage),
                         ("fci", 2, "201812010103", 12, "chs"))

    def test_lan_and_isolated_ports_keep_all_other_protobuf_fields(self):
        raw = FIXTURE.read_bytes()
        before = config_message(plaintext(raw))
        for port in (19080, 19082):
            with self.subTest(port=port):
                target = f"http://192.168.1.22:{port}/pr2new_djwk.json"
                output, report = patch.patch_bundle(raw, target)
                after = config_message(plaintext(output))
                self.assertEqual(list(after.localedConfigUrl), [target])
                after.localedConfigUrl[0] = patch.ORIGINAL_URL
                # Comparing independent protobuf serialization includes fields
                # not modeled here, such as the original empty debug message.
                self.assertEqual(after.SerializeToString(), before.SerializeToString())
                self.assertEqual(report["bootstrapUrl"], target)
                self.assertTrue(report["configRoundTrip"])
                self.assertEqual(output[:12], b"UnityFS\x00\x00\x00\x00\x06")

    def test_cli_writes_separate_bundle_without_touching_input_apk(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            apk, output, report = folder / "original.apk", folder / "bundle", folder / "report.json"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr(patch.ENTRY, FIXTURE.read_bytes())
            before = apk.read_bytes()
            self.assertEqual(patch.main(["--apk", str(apk), "--output-bundle", str(output),
                                         "--report", str(report)]), 0)
            self.assertEqual(apk.read_bytes(), before)
            self.assertEqual(list(config_message(plaintext(output.read_bytes())).localedConfigUrl),
                             [patch.DEFAULT_URL])
            with self.assertRaises(SystemExit):
                patch.main(["--apk", str(apk), "--output-bundle", str(apk), "--report", str(report)])
            self.assertEqual(apk.read_bytes(), before)

    def test_wrong_source_or_malformed_config_is_rejected(self):
        with self.assertRaises(ValueError):
            patch.patch_bundle(FIXTURE.read_bytes() + b"unexpected")
        original = plaintext(FIXTURE.read_bytes())
        for malformed in (original[:-1], original + b"\x22\x01x", b"\x22\xff", b"\x00"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                patch.replace_url(malformed, patch.DEFAULT_URL)

    def test_url_validation_prevents_wrong_bootstrap_requests(self):
        original = plaintext(FIXTURE.read_bytes())
        for url in ("/pr2new_djwk.json", "ftp://192.168.1.22/pr2new_djwk.json",
                    "http://192.168.1.22/other.json", "http://user:pass@192.168.1.22/pr2new_djwk.json",
                    "http://192.168.1.22:99999/pr2new_djwk.json", patch.DEFAULT_URL + "#fragment"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                patch.replace_url(original, url)


if __name__ == "__main__":
    unittest.main()
