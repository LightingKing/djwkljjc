"""Validate binary Manifest edits with independent Androguard/aapt readers."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

try:
    from loguru import logger
    logger.disable("androguard")
    from androguard.core.axml import AXMLPrinter
except ImportError:
    AXMLPrinter = None


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lan_patch_manifest", ROOT / "client-patch/lan/patch_manifest.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
ANDROID = "http://schemas.android.com/apk/res/android"
NS = "{" + ANDROID + "}"
OLD_PACKAGE = "com.tencent.tmgp.fcicv"
ORIGINAL = Path(os.environ.get("LJJC_ORIGINAL_MANIFEST", str(ROOT.parents[1] / "analysis/ljjc_manifest_out/original/AndroidManifest.xml")))


def binary_fixture(utf8=False):
    """A tiny independent AXML fixture, with shared strings and class shorthand."""
    strings = []

    def index(value):
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    def node(tag, attrs=(), end=False):
        tag_index = index(tag)
        body = struct.pack("<II", 0xFFFFFFFF, tag_index)
        if not end:
            body += struct.pack("<6H", 20, 20, len(attrs), 0, 0, 0)
            for name, value, kind in attrs:
                namespace = 0xFFFFFFFF if name == "package" else index(ANDROID)
                name_index = index(name)
                data = index(value) if kind == 3 else value
                raw_index = data if kind == 3 else 0xFFFFFFFF
                body += struct.pack("<IIIHBBI", namespace, name_index, raw_index, 8, 0, kind, data)
        return struct.pack("<HHIII", 0x103 if end else 0x102, 16, 16 + len(body), 1, 0xFFFFFFFF) + body

    index("android")
    index(ANDROID)
    nodes = [node("manifest", (("package", OLD_PACKAGE, 3), ("versionName", "2.0.4", 3))),
             node("uses-sdk", (("minSdkVersion", 18, 16), ("targetSdkVersion", 22, 16))),
             node("uses-sdk", end=True),
             node("application", (("label", 0x7F0C0021, 1), ("icon", 0x7F070053, 1),
                                  ("theme", 0x7F0D011F, 1), ("name", ".App", 3))),
             node("activity", (("name", ".MainActivity", 3), ("taskAffinity", OLD_PACKAGE + ".diff", 3),
                               ("label", "Other activity", 3))),
             node("activity", end=True),
             node("activity", (("name", OLD_PACKAGE + ".wxapi.WXEntryActivity", 3),)),
             node("activity", end=True),
             node("activity", (("name", "com.actgames.fci.MainActivity", 3), ("label", 0x7F0C0021, 1))),
             node("activity", end=True),
             node("provider", (("name", "Provider", 3),
                               ("authorities", OLD_PACKAGE + ".AUTH;other.provider;" + OLD_PACKAGE + "X.provider", 3))),
             node("provider", end=True), node("application", end=True), node("manifest", end=True)]
    encoded = bytearray()
    offsets = []
    for text in strings:
        offsets.append(len(encoded))
        if utf8:
            data = text.encode("utf-8")
            # All fixture strings fit the one-byte form. The patch adds a long
            # UTF-8 label below, exercising its two-byte length writer.
            assert len(data) < 128
            encoded += bytes((len(text), len(data))) + data + b"\0"
        else:
            data = text.encode("utf-16-le")
            encoded += struct.pack("<H", len(data) // 2) + data + b"\0\0"
    encoded += b"\0" * (-len(encoded) % 4)
    pool_size = 28 + 4 * len(strings) + len(encoded)
    pool = struct.pack("<HH6I", 1, 28, pool_size, len(strings), 0, 0x100 if utf8 else 0, 28 + 4 * len(strings), 0)
    pool += b"".join(struct.pack("<I", n) for n in offsets) + encoded
    ns_begin = struct.pack("<HHIIIII", 0x100, 16, 24, 1, 0xFFFFFFFF, index("android"), index(ANDROID))
    ns_end = struct.pack("<HHIIIII", 0x101, 16, 24, 1, 0xFFFFFFFF, index("android"), index(ANDROID))
    body = pool + ns_begin + b"".join(nodes) + ns_end
    return struct.pack("<HHI", 3, 8, len(body) + 8) + body


def parse_independently(raw):
    parsed = AXMLPrinter(raw)
    if not parsed.is_valid():
        raise AssertionError("independent Androguard parser rejected modified AXML")
    return parsed.get_xml_obj()


@unittest.skipIf(AXMLPrinter is None, "independent binary XML validation requires androguard")
class LanManifestPatchTests(unittest.TestCase):
    def test_both_string_encodings_preserve_resources_and_java_class_resolution(self):
        for utf8 in (False, True):
            with self.subTest(utf8=utf8):
                original = binary_fixture(utf8)
                before = parse_independently(original)
                label = "零境交错·内网版" + "😀" * 40
                output, report = PATCH.patch_manifest(original, package_name="local.ljjc.rehearsal", label=label)
                after = parse_independently(output)
                self.assertEqual(after.get("package"), "local.ljjc.rehearsal")
                self.assertEqual(after.get(NS + "versionName"), "2.0.4-lan12")
                self.assertEqual(after.find("uses-sdk").get(NS + "targetSdkVersion"), "24")
                self.assertEqual(after.find("uses-sdk").get(NS + "minSdkVersion"), "18")
                app = after.find("application")
                self.assertEqual(app.get(NS + "label"), label)
                for name in ("icon", "theme"):
                    self.assertEqual(app.get(NS + name), before.find("application").get(NS + name))
                self.assertEqual(app.get(NS + "name"), OLD_PACKAGE + ".App")
                activities = app.findall("activity")
                self.assertEqual(activities[0].get(NS + "name"), OLD_PACKAGE + ".MainActivity")
                self.assertEqual(activities[0].get(NS + "taskAffinity"), "local.ljjc.rehearsal.diff")
                self.assertEqual(activities[0].get(NS + "label"), "Other activity")
                self.assertEqual(activities[1].get(NS + "name"), OLD_PACKAGE + ".wxapi.WXEntryActivity")
                self.assertEqual(activities[2].get(NS + "name"), "com.actgames.fci.MainActivity")
                self.assertEqual(activities[2].get(NS + "label"), label)
                self.assertEqual(app.find("provider").get(NS + "name"), OLD_PACKAGE + ".Provider")
                self.assertEqual(app.find("provider").get(NS + "authorities"),
                                 "local.ljjc.rehearsal.AUTH;other.provider;" + OLD_PACKAGE + "X.provider")
                metadata = [e for e in app.findall("meta-data")
                            if e.get(NS + "name") == "unityplayer.SkipPermissionsDialog"]
                self.assertEqual(len(metadata), 1)
                self.assertEqual(metadata[0].get(NS + "value"), "true")
                self.assertEqual(report["inputSha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(report["outputSha256"], hashlib.sha256(output).hexdigest())
                self.assertEqual(PATCH.patch_manifest(original, package_name="local.ljjc.rehearsal", label=label)[0], output)

    def test_repeated_patch_is_idempotent(self):
        once, _ = PATCH.patch_manifest(binary_fixture(), version_name="2.0.4")
        twice, report = PATCH.patch_manifest(once, version_name="2.0.4")
        self.assertEqual(twice, once)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["addedStrings"], 0)

    def test_permissions_metadata_can_be_disabled_and_updated_without_duplicates(self):
        original = binary_fixture()
        disabled, _ = PATCH.patch_manifest(original, skip_permissions_dialog=False)
        self.assertEqual(parse_independently(disabled).find("application").findall("meta-data"), [])
        enabled, _ = PATCH.patch_manifest(disabled)
        disabled, report = PATCH.patch_manifest(enabled, skip_permissions_dialog=False)
        entries = parse_independently(disabled).find("application").findall("meta-data")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].get(NS + "value"), "false")
        self.assertEqual(len(report["changes"]), 1)
        enabled_again, report = PATCH.patch_manifest(disabled)
        self.assertEqual(enabled_again, enabled)
        self.assertEqual(len(report["changes"]), 1)

    def test_invalid_or_unexpected_inputs_fail_before_emitting_a_manifest(self):
        original = binary_fixture()
        for raw in (b"", b"<manifest/>", original[:-1], original + b"\0"):
            with self.subTest(raw_length=len(raw)), self.assertRaises(ValueError):
                PATCH.patch_manifest(raw)
        for kwargs in ({"package_name": "not a package"}, {"package_name": "local.1bad"},
                       {"label": ""}, {"version_name": "x\0y"},
                       {"target_sdk": 22}, {"target_sdk": True}, {"skip_permissions_dialog": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PATCH.patch_manifest(original, **kwargs)
        unexpected_target, _ = PATCH.patch_manifest(original, target_sdk=28)
        with self.assertRaisesRegex(ValueError, "targetSdkVersion"):
            PATCH.patch_manifest(unexpected_target)

    @unittest.skipUnless(ORIGINAL.is_file(), "original game Manifest not available in this checkout")
    def test_original_game_changes_only_the_requested_attributes(self):
        raw = ORIGINAL.read_bytes()
        output, report = PATCH.patch_manifest(raw, version_name="2.0.4")
        before = list(parse_independently(raw).iter())
        after = list(parse_independently(output).iter())
        metadata = [node for node in after if node.tag == "meta-data"
                    and node.get(NS + "name") == "unityplayer.SkipPermissionsDialog"]
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0].getparent().tag, "application")
        self.assertEqual(metadata[0].get(NS + "value"), "true")
        after = [node for node in after if node not in metadata]
        self.assertEqual(len(before), len(after))
        edits = []
        for old, new in zip(before, after):
            self.assertEqual(old.tag, new.tag)
            self.assertEqual(set(old.attrib), set(new.attrib))
            expected = dict(old.attrib)
            if old.tag == "manifest":
                expected["package"] = "local.ljjc.lan"
            elif old.tag == "uses-sdk":
                expected[NS + "targetSdkVersion"] = "24"
            elif old.tag == "application":
                expected[NS + "label"] = "零境交错·内网版"
            elif old.tag == "activity" and old.get(NS + "name") == "com.actgames.fci.MainActivity":
                expected[NS + "label"] = "零境交错·内网版"
            for name in ("authorities", "taskAffinity"):
                key = NS + name
                if expected.get(key, "").startswith(OLD_PACKAGE + "."):
                    expected[key] = "local.ljjc.lan" + expected[key][len(OLD_PACKAGE):]
            self.assertEqual(dict(new.attrib), expected, old.tag)
            for key, value in old.attrib.items():
                if value != new.attrib[key]:
                    edits.append((old.tag, key))
        self.assertEqual(len(edits), 10)
        self.assertEqual(len(report["changes"]), 11)
        providers = [node.get(NS + "authorities") for node in after if node.tag == "provider"]
        self.assertEqual(len(providers), 3)
        self.assertTrue(all(value.startswith("local.ljjc.lan.") for value in providers))
        self.assertEqual([dict(e.attrib) for e in before if e.tag == "uses-permission"],
                         [dict(e.attrib) for e in after if e.tag == "uses-permission"])

    @unittest.skipUnless(ORIGINAL.is_file(), "original game Manifest not available in this checkout")
    def test_aapt_reads_the_modified_original_without_rebuilt_resources(self):
        aapt = shutil.which("aapt")
        if not aapt:
            sdk = Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/build-tools"
            candidates = sorted(sdk.glob("*/aapt.exe"))
            aapt = str(candidates[-1]) if candidates else None
        if not aapt:
            self.skipTest("Android build-tools aapt is not installed")
        output, _ = PATCH.patch_manifest(ORIGINAL.read_bytes(), version_name="2.0.4")
        # A tiny ZIP containing only this 22KB XML is sufficient for aapt's XML
        # reader; no APK/resources build or installation takes place.
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "manifest-only.zip"
            with zipfile.ZipFile(candidate, "w") as archive:
                archive.writestr("AndroidManifest.xml", output)
            result = subprocess.run([aapt, "dump", "xmltree", str(candidate), "AndroidManifest.xml"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        text = result.stdout.decode("utf-8")
        self.assertIn('package="local.ljjc.lan"', text)
        self.assertIn('"零境交错·内网版"', text)
        self.assertEqual(text.count('label(0x01010001)="零境交错·内网版"'), 2)
        self.assertIn("targetSdkVersion(0x01010270)=(type 0x10)0x18", text)
        self.assertIn("icon(0x01010002)=@0x7f070053", text)
        self.assertIn("theme(0x01010000)=@0x7f0d011f", text)
        self.assertIn(OLD_PACKAGE + ".wxapi.WXEntryActivity", text)
        self.assertIn('"unityplayer.SkipPermissionsDialog"', text)
        self.assertIn("value(0x01010024)=(type 0x12)0xffffffff", text)
