"""Patch only the original GameConstConfig bootstrap URL into a separate bundle.

The APK stays read-only. The caller must assemble/sign a candidate APK and verify
it on a device; an offline round-trip is not evidence that a candidate starts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hot-update"))
import unityfs_bundle as unityfs

ENTRY = "assets/Patch/assetbundle/data/configproto/gameconstconfig"
ORIGINAL_SHA256 = "43d63c7d0696fcdbfcc22962dd66cd7666206eaa679cc0ccd8f0380e7a3e8f23"
ORIGINAL_URL = "https://prds.djwk.qq.com:9102/pr2new_djwk.json"
DEFAULT_URL = "http://192.168.1.22:19080/pr2new_djwk.json"


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise ValueError("protobuf varint overflow")
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise ValueError("protobuf varint overflow")


def varint(value: int) -> bytes:
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def fields(data: bytes):
    """Yield field number, wire type, value and original span; preserve unknowns."""
    offset = 0
    while offset < len(data):
        start = offset
        tag, offset = read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            value, offset = read_varint(data, offset)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, offset = read_varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            if length > len(data) - offset:
                raise ValueError("truncated protobuf field")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire}")
        yield number, wire, value, data[start:offset]


def decode_config(raw: bytes) -> tuple[bytes, list[int]]:
    # GameConstConfig starts with protobuf field 1, appId="fci". Lua's
    # plaintext magic is different and its automatic key recovery cannot apply.
    if not raw.startswith(b"FCI\0") or len(raw) < 9:
        raise ValueError("expected FCI GameConstConfig payload")
    body = raw[4:]
    key = [body[i] ^ b"\x0a\x03fc"[i] for i in range(4)]
    plain = bytes(byte ^ key[(i % 4 + i // 4) % 4] for i, byte in enumerate(body))
    parsed = list(fields(plain))
    if parsed[0][:3] != (1, 2, b"fci"):
        raise ValueError("unexpected GameConstConfig appId")
    if unityfs.encode_fci(plain, key) != raw:
        raise ValueError("FCI round-trip failed")
    return plain, key


def replace_url(plain: bytes, target: str) -> tuple[bytes, str]:
    parsed_url = urlsplit(target)
    if (parsed_url.scheme not in ("http", "https") or not parsed_url.hostname
            or parsed_url.username is not None or parsed_url.password is not None
            or parsed_url.path != "/pr2new_djwk.json" or parsed_url.query
            or parsed_url.fragment or any(char.isspace() for char in target)):
        raise ValueError("bootstrap URL must be an absolute HTTP(S) /pr2new_djwk.json URL")
    # Accessing .port also rejects malformed/out-of-range explicit ports.
    parsed_url.port
    encoded = target.encode("ascii")
    parts = list(fields(plain))
    urls = [(wire, value) for number, wire, value, _ in parts if number == 4]
    if len(urls) != 1 or urls[0][0] != 2:
        raise ValueError("expected exactly one length-delimited localedConfigUrl")
    previous = urls[0][1].decode("ascii")
    output = b"".join(b"\x22" + varint(len(encoded)) + encoded if number == 4 else raw
                      for number, _, _, raw in parts)
    if [raw for number, _, _, raw in fields(output) if number != 4] != [
            raw for number, _, _, raw in parts if number != 4]:
        raise ValueError("non-URL config fields changed")
    return output, previous


def text_asset(bundle: bytes):
    import UnityPy

    env = UnityPy.load(bundle)
    assets = [obj for obj in env.objects if obj.type.name == "TextAsset"]
    if len(assets) != 1:
        raise ValueError("expected exactly one TextAsset")
    tree = assets[0].read_typetree()
    if tree["m_Name"] != "GameConstConfig":
        raise ValueError("unexpected config TextAsset name")
    return env, assets[0], tree


def patch_bundle(original: bytes, target: str = DEFAULT_URL) -> tuple[bytes, dict]:
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("bundle is not the verified original 2.0.4 GameConstConfig")
    info = unityfs.parse(original)
    if len(info["nodes"]) != 1 or info["nodes"][0][0] != 0:
        raise ValueError("expected original single-node UnityFS bundle")
    env, obj, tree = text_asset(original)
    plain, key = decode_config(tree["m_Script"].encode("utf-8", "surrogateescape"))
    changed, previous = replace_url(plain, target)
    if previous != ORIGINAL_URL:
        raise ValueError("unexpected original bootstrap URL")
    tree["m_Script"] = unityfs.encode_fci(changed, key).decode("utf-8", "surrogateescape")
    obj.save_typetree(tree)
    # UnityPy resizes the serialized object; the existing Unity 5.6-compatible
    # writer emits the outer archive (UnityPy's outer archive is incompatible).
    resized = unityfs.parse(env.file.save())["data"]
    offset, _, flags, name = info["nodes"][0]
    info["nodes"] = [(offset, len(resized), flags, name)]
    output = unityfs.build_uncompressed(info, resized)
    _, _, checked_tree = text_asset(output)
    checked, checked_key = decode_config(checked_tree["m_Script"].encode("utf-8", "surrogateescape"))
    if checked != changed or checked_key != key or unityfs.parse(output)["nodes"] != info["nodes"]:
        raise ValueError("patched config did not survive bundle round-trip")
    return output, {
        "entry": ENTRY, "originalBundleSha256": ORIGINAL_SHA256,
        "bundleSha256": hashlib.sha256(output).hexdigest(), "bundleLength": len(output),
        "originalBootstrapUrl": previous, "bootstrapUrl": target,
        "configRoundTrip": True, "nonUrlFieldsUnchanged": True,
        "deviceValidation": "not performed by this tool",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--bootstrap-url", default=DEFAULT_URL)
    parser.add_argument("--output-bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = [path.resolve() for path in (args.apk, args.output_bundle, args.report)]
    if len(set(paths)) != 3:
        parser.error("input APK, output bundle and report must be different paths")
    with zipfile.ZipFile(args.apk) as archive:
        bundle, report = patch_bundle(archive.read(ENTRY), args.bootstrap_url)
    report["sourceApk"] = str(paths[0])
    for path in (args.output_bundle, args.report):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output_bundle.write_bytes(bundle)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
