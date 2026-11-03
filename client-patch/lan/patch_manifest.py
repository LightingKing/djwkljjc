"""Patch the original binary Manifest without rebuilding Android resources.

Only selected attribute values and Unity's permissions-dialog metadata change.
String indices, resource IDs, Java class names and all other XML chunks retain
their original meaning. This module
does not assemble/sign an APK, install anything, or change a running service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


ANDROID = "http://schemas.android.com/apk/res/android"
ORIGINAL_PACKAGE = "com.tencent.tmgp.fcicv"
NO_INDEX = 0xFFFFFFFF
TYPE_STRING = 3
TYPE_INT_DEC = 16
TYPE_INT_BOOLEAN = 18
SKIP_PERMISSIONS_KEY = "unityplayer.SkipPermissionsDialog"


def _u32(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _chunks(raw: bytes) -> list[bytearray]:
    if len(raw) < 8 or struct.unpack_from("<HHI", raw) != (3, 8, len(raw)):
        raise ValueError("expected a complete binary Android XML document")
    output = []
    offset = 8
    while offset < len(raw):
        if len(raw) - offset < 8:
            raise ValueError("truncated XML chunk header")
        _, header_size, size = struct.unpack_from("<HHI", raw, offset)
        if not 8 <= header_size <= size <= len(raw) - offset or size % 4:
            raise ValueError("invalid XML chunk length")
        output.append(bytearray(raw[offset:offset + size]))
        offset += size
    return output


class _StringPool:
    def __init__(self, raw: bytearray):
        self.raw = raw
        if len(raw) < 28:
            raise ValueError("truncated XML string pool")
        self.header_size = struct.unpack_from("<H", raw, 2)[0]
        self.count, self.style_count, self.flags, self.start, self.styles = struct.unpack_from("<5I", raw, 8)
        table_end = self.header_size + 4 * (self.count + self.style_count)
        self.end = self.styles or len(raw)
        if not 28 <= self.header_size <= table_end <= self.start <= self.end <= len(raw):
            raise ValueError("invalid XML string pool offsets")
        if bool(self.styles) != bool(self.style_count):
            raise ValueError("inconsistent XML string styles")
        self.offsets = [_u32(raw, self.header_size + i * 4) for i in range(self.count)]
        self.strings = [self._decode(i) for i in range(self.count)]
        self.additions: list[str] = []

    def _length(self, offset: int, utf8: bool) -> tuple[int, int]:
        width, flag, mask = (1, 0x80, 0x7F) if utf8 else (2, 0x8000, 0x7FFF)
        if offset + width > self.end:
            raise ValueError("truncated XML string length")
        first = int.from_bytes(self.raw[offset:offset + width], "little")
        offset += width
        if first & flag:
            if offset + width > self.end:
                raise ValueError("truncated XML long string length")
            second = int.from_bytes(self.raw[offset:offset + width], "little")
            return ((first & mask) << (width * 8)) | second, offset + width
        return first, offset

    def _decode(self, index: int) -> str:
        offset = self.start + self.offsets[index]
        if not self.start <= offset < self.end:
            raise ValueError("XML string points outside pool")
        utf8 = bool(self.flags & 0x100)
        length, offset = self._length(offset, utf8)
        if utf8:
            length, offset = self._length(offset, True)
        size = length if utf8 else length * 2
        terminator = 1 if utf8 else 2
        if offset + size + terminator > self.end or any(self.raw[offset + size:offset + size + terminator]):
            raise ValueError("unterminated XML string")
        return bytes(self.raw[offset:offset + size]).decode("utf-8" if utf8 else "utf-16-le")

    def get(self, index: int) -> str | None:
        if index == NO_INDEX:
            return None
        if not 0 <= index < len(self.strings):
            raise ValueError("invalid XML string index")
        return self.strings[index]

    def add(self, value: str) -> int:
        if value in self.strings:
            return self.strings.index(value)
        self.strings.append(value)
        self.additions.append(value)
        return len(self.strings) - 1

    @staticmethod
    def _encode_length(value: int, utf8: bool) -> bytes:
        if utf8:
            if value > 0x7FFF:
                raise ValueError("UTF-8 XML string exceeds format limit")
            return bytes((value,)) if value < 0x80 else bytes(((value >> 8) | 0x80, value & 255))
        if value > 0x7FFFFFFF:
            raise ValueError("UTF-16 XML string exceeds format limit")
        return struct.pack("<H", value) if value < 0x8000 else struct.pack("<HH", (value >> 16) | 0x8000, value & 0xFFFF)

    def build(self) -> bytearray:
        if not self.additions:
            return self.raw
        string_data = bytearray(self.raw[self.start:self.end])
        new_offsets = []
        for value in self.additions:
            new_offsets.append(len(string_data))
            utf16 = value.encode("utf-16-le")
            if self.flags & 0x100:
                encoded = value.encode("utf-8")
                string_data += self._encode_length(len(utf16) // 2, True)
                string_data += self._encode_length(len(encoded), True) + encoded + b"\0"
            else:
                string_data += self._encode_length(len(utf16) // 2, False) + utf16 + b"\0\0"
        string_data += b"\0" * (-len(string_data) % 4)
        header = bytearray(self.raw[:self.header_size])
        # Append strings without moving existing indices. Appending invalidates
        # the optional sorted flag, but does not alter resource-map indices.
        struct.pack_into("<I", header, 8, len(self.strings))
        struct.pack_into("<I", header, 16, self.flags & ~1)
        new_start = self.start + 4 * len(new_offsets)
        struct.pack_into("<II", header, 20, new_start, new_start + len(string_data) if self.styles else 0)
        table_end = self.header_size + self.count * 4
        output = header + self.raw[self.header_size:table_end]
        output += b"".join(struct.pack("<I", value) for value in new_offsets)
        output += self.raw[table_end:self.start] + string_data + self.raw[self.end:]
        struct.pack_into("<I", output, 4, len(output))
        return output


def _attributes(chunk: bytearray, pool: _StringPool):
    header_size = struct.unpack_from("<H", chunk, 2)[0]
    if header_size < 16 or header_size + 20 > len(chunk):
        raise ValueError("truncated XML start element")
    tag = pool.get(_u32(chunk, header_size + 4))
    start, size, count = struct.unpack_from("<HHH", chunk, header_size + 8)
    if start < 20 or size < 20 or header_size + start + size * count > len(chunk):
        raise ValueError("invalid XML attribute layout")
    for i in range(count):
        offset = header_size + start + i * size
        ns, name, raw_index = struct.unpack_from("<III", chunk, offset)
        value_size, reserved, value_type, value = struct.unpack_from("<HBBI", chunk, offset + 12)
        if value_size != 8 or reserved:
            raise ValueError("unsupported XML attribute value")
        yield tag, pool.get(ns), pool.get(name), offset, value_type, value, raw_index


def _set_skip_permissions_dialog(chunks: list[bytearray], pool: _StringPool,
                                 enabled: bool, changes: list[dict]) -> None:
    """Set Unity's documented manifest switch without granting permissions."""
    stack = []
    matches = []
    application_end = None
    for chunk_index, chunk in enumerate(chunks):
        kind, header_size = struct.unpack_from("<HH", chunk)
        if kind not in (0x102, 0x103):
            continue
        if header_size < 16 or header_size + 8 > len(chunk):
            raise ValueError("truncated XML element")
        tag = pool.get(_u32(chunk, header_size + 4))
        if kind == 0x102:
            if tag == "meta-data" and stack == ["manifest", "application"]:
                attrs = list(_attributes(chunk, pool))
                names = [a for a in attrs if a[1:3] == (ANDROID, "name")]
                if len(names) == 1 and names[0][4] == TYPE_STRING and pool.get(names[0][5]) == SKIP_PERMISSIONS_KEY:
                    matches.append((chunk_index, attrs))
            stack.append(tag)
        else:
            if not stack or stack.pop() != tag:
                raise ValueError("unbalanced XML elements")
            if tag == "application" and stack == ["manifest"]:
                application_end = chunk_index
    if stack or application_end is None or len(matches) > 1:
        raise ValueError("expected one application and at most one Unity permissions metadata entry")
    if matches:
        chunk_index, attrs = matches[0]
        values = [a for a in attrs if a[1:3] == (ANDROID, "value")]
        if len(values) != 1:
            raise ValueError("existing Unity permissions metadata must contain one android:value")
        _, _, _, offset, value_type, value, _ = values[0]
        desired = 0xFFFFFFFF if enabled else 0
        if value_type == TYPE_INT_BOOLEAN and bool(value) == enabled:
            return
        chunk = chunks[chunk_index]
        struct.pack_into("<I", chunk, offset + 8, NO_INDEX)
        struct.pack_into("<HBBI", chunk, offset + 12, 8, 0, TYPE_INT_BOOLEAN, desired)
        changes.append({"chunk": chunk_index, "element": "meta-data", "attribute": "value",
                        "name": SKIP_PERMISSIONS_KEY, "beforeType": value_type,
                        "before": pool.get(value) if value_type == TYPE_STRING else value,
                        "after": enabled})
        return
    if not enabled:
        return
    namespace = pool.add(ANDROID)
    tag = pool.add("meta-data")
    name = pool.add("name")
    value = pool.add("value")
    key = pool.add(SKIP_PERMISSIONS_KEY)
    # Attribute names need framework IDs as well as strings, otherwise Android
    # would parse the XML but not recognize android:name / android:value.
    resource_maps = [i for i, c in enumerate(chunks) if struct.unpack_from("<H", c)[0] == 0x180]
    if len(resource_maps) > 1:
        raise ValueError("multiple XML resource maps")
    resource_ids = []
    if resource_maps:
        resource_map = chunks[resource_maps[0]]
        if struct.unpack_from("<H", resource_map, 2)[0] != 8:
            raise ValueError("unsupported XML resource map header")
        resource_ids = list(struct.unpack_from(f"<{(len(resource_map) - 8) // 4}I", resource_map, 8))
    resource_ids += [0] * max(0, max(name, value) + 1 - len(resource_ids))
    for index, resource_id in ((name, 0x01010003), (value, 0x01010024)):
        if resource_ids[index] not in (0, resource_id):
            raise ValueError("conflicting framework attribute resource ID")
        resource_ids[index] = resource_id
    new_resource_map = bytearray(struct.pack("<HHI", 0x180, 8, 8 + 4 * len(resource_ids)))
    new_resource_map += struct.pack(f"<{len(resource_ids)}I", *resource_ids)
    line = _u32(chunks[application_end], 8)
    attributes = struct.pack("<IIIHBBI", namespace, name, key, 8, 0, TYPE_STRING, key)
    attributes += struct.pack("<IIIHBBI", namespace, value, NO_INDEX, 8, 0, TYPE_INT_BOOLEAN, 0xFFFFFFFF)
    start = bytearray(struct.pack("<HHIII", 0x102, 16, 76, line, NO_INDEX))
    start += struct.pack("<II6H", NO_INDEX, tag, 20, 20, 2, 0, 0, 0) + attributes
    end = bytearray(struct.pack("<HHIIIII", 0x103, 16, 24, line, NO_INDEX, NO_INDEX, tag))
    chunks[application_end:application_end] = [start, end]
    if resource_maps:
        chunks[resource_maps[0]] = new_resource_map
    else:
        pool_index = next(i for i, c in enumerate(chunks) if struct.unpack_from("<H", c)[0] == 1)
        chunks.insert(pool_index + 1, new_resource_map)
    changes.append({"element": "meta-data", "attribute": "value", "name": SKIP_PERMISSIONS_KEY,
                    "before": None, "after": True, "action": "insert"})


def patch_manifest(raw: bytes, *, package_name: str = "local.ljjc.lan",
                   label: str = "零境交错·内网版", version_name: str = "2.0.4-lan12",
                   target_sdk: int = 24, skip_permissions_dialog: bool = True) -> tuple[bytes, dict]:
    """Return modified AXML and an exact change report; leave input untouched.

    The game may derive its hot-update baseline from versionName. Callers must
    explicitly retain version_name="2.0.4" when using that original baseline.
    """
    if not isinstance(package_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package_name):
        raise ValueError("invalid Android package name")
    if any(not isinstance(value, str) or not value or "\0" in value for value in (label, version_name)):
        raise ValueError("label and versionName must be nonempty strings without NUL")
    if type(target_sdk) is not int or not 24 <= target_sdk <= 0x7FFFFFFF:
        raise ValueError("target SDK must be an integer of at least 24")
    if type(skip_permissions_dialog) is not bool:
        raise ValueError("skip_permissions_dialog must be a boolean")
    chunks = _chunks(raw)
    pools = [i for i, c in enumerate(chunks) if struct.unpack_from("<H", c)[0] == 1]
    if len(pools) != 1:
        raise ValueError("expected exactly one XML string pool")
    pool_index = pools[0]
    pool = _StringPool(chunks[pool_index])
    changes = []
    required = {("manifest", None, "package"): 0,
                ("manifest", ANDROID, "versionName"): 0,
                ("uses-sdk", ANDROID, "targetSdkVersion"): 0,
                ("application", ANDROID, "label"): 0}
    for chunk_index, chunk in enumerate(chunks):
        if struct.unpack_from("<H", chunk)[0] != 0x102:
            continue
        attributes = list(_attributes(chunk, pool))
        is_launcher_activity = any(
            tag == "activity" and ns == ANDROID and name == "name"
            and value_type == TYPE_STRING and pool.get(value) == "com.actgames.fci.MainActivity"
            for tag, ns, name, _, value_type, value, _ in attributes
        )
        for tag, ns, name, offset, value_type, value, raw_index in attributes:
            key = (tag, ns, name)
            before = pool.get(value) if value_type == TYPE_STRING else value
            replacement = None
            if key in required:
                required[key] += 1
                if name == "package":
                    if before not in (ORIGINAL_PACKAGE, package_name):
                        raise ValueError("Manifest is not the expected game package")
                    replacement = package_name
                elif name == "versionName":
                    replacement = version_name
                elif name == "label":
                    replacement = label
                else:
                    if value_type not in (TYPE_INT_DEC, 17) or value not in (22, target_sdk):
                        raise ValueError("expected original targetSdkVersion 22 or requested target")
                    replacement = target_sdk
            elif is_launcher_activity and ns == ANDROID and name == "label":
                # The original MAIN/LAUNCHER activity overrides application.label.
                # Keep SDK/secondary activity labels unchanged.
                replacement = label
            elif ns == ANDROID and name in ("authorities", "taskAffinity") and value_type == TYPE_STRING:
                parts = before.split(";") if name == "authorities" else [before]
                revised = [package_name + part[len(ORIGINAL_PACKAGE):]
                           if part == ORIGINAL_PACKAGE or part.startswith(ORIGINAL_PACKAGE + ".") else part
                           for part in parts]
                if revised != parts:
                    replacement = ";".join(revised)
            elif ns == ANDROID and value_type == TYPE_STRING:
                # Renaming the package changes resolution of shorthand class
                # names. Keep their original absolute class instead of moving
                # Java classes; fully qualified WX/SDK names stay untouched.
                class_attr = (name == "name" and tag in ("application", "activity", "activity-alias", "service", "receiver", "provider", "instrumentation"))
                class_attr |= name in ("backupAgent", "appComponentFactory", "manageSpaceActivity", "targetActivity", "parentActivityName")
                if class_attr and before and (before.startswith(".") or "." not in before):
                    replacement = ORIGINAL_PACKAGE + ("" if before.startswith(".") else ".") + before
            if replacement is None or replacement == before:
                continue
            if isinstance(replacement, str):
                index = pool.add(replacement)
                struct.pack_into("<I", chunk, offset + 8, index)
                struct.pack_into("<HBBI", chunk, offset + 12, 8, 0, TYPE_STRING, index)
            else:
                struct.pack_into("<I", chunk, offset + 8, NO_INDEX)
                struct.pack_into("<HBBI", chunk, offset + 12, 8, 0, TYPE_INT_DEC, replacement)
            changes.append({"chunk": chunk_index, "element": tag, "attribute": name,
                            "beforeType": value_type, "before": before, "after": replacement})
    if any(count != 1 for count in required.values()):
        raise ValueError("expected one package, versionName, targetSdkVersion and application label")
    _set_skip_permissions_dialog(chunks, pool, skip_permissions_dialog, changes)
    chunks[pool_index] = pool.build()
    body = b"".join(chunks)
    output = struct.pack("<HHI", 3, 8, len(body) + 8) + body
    report = {"status": "offline-candidate", "published": False,
              "inputSha256": hashlib.sha256(raw).hexdigest(),
              "outputSha256": hashlib.sha256(output).hexdigest(),
              "inputLength": len(raw), "outputLength": len(output),
              "package": package_name, "label": label, "versionName": version_name,
              "targetSdkVersion": target_sdk, "addedStrings": len(pool.additions),
              "skipPermissionsDialog": skip_permissions_dialog,
              "changes": changes}
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="original binary AndroidManifest.xml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--package-name", default="local.ljjc.lan")
    parser.add_argument("--label", default="零境交错·内网版")
    parser.add_argument("--version-name", default="2.0.4-lan12")
    parser.add_argument("--target-sdk", type=int, default=24)
    parser.add_argument("--skip-permissions-dialog", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.input.resolve() in (args.output.resolve(), args.report.resolve()) or args.output.resolve() == args.report.resolve():
        parser.error("input, output and report paths must be separate")
    output, report = patch_manifest(args.input.read_bytes(), package_name=args.package_name,
                                    label=args.label, version_name=args.version_name, target_sdk=args.target_sdk,
                                    skip_permissions_dialog=args.skip_permissions_dialog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
