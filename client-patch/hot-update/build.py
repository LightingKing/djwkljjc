"""Build and verify an offline hot-update candidate; never publish it.

Inputs are explicit paths. Outputs are bundles, a ZIP, patch-manifest.json,
and build-report.json below --output-dir. No running service or client is
contacted, and the online force-patch.json is never written.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import struct
import sys
import zipfile
import zlib

import fci_lua_codec as codec
import unityfs_bundle as unityfs


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("module paths must be POSIX relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in value.split("/")) or "//" in value:
        raise ValueError("unsafe module path")
    return str(path)


def compile_source(source: bytes, chunkname: bytes) -> bytes:
    import lupa.lua53 as lua53

    runtime = lua53.LuaRuntime(encoding=None)
    compile_lua = runtime.eval(b"function(s,n) local f,e=load(s,n); if not f then error(e) end; return string.dump(f,false) end")
    return bytes(compile_lua(source, chunkname))


def verify_lua(standard: bytes) -> None:
    import lupa.lua53 as lua53

    runtime = lua53.LuaRuntime(encoding=None)
    valid = runtime.eval(b"function(b) local f,e=load(b); return f ~= nil end")(standard)
    if not valid:
        raise ValueError("compiled Lua payload cannot be loaded")


def original_chunkname(container: bytes) -> bytes:
    # FCI header(4), main upvalue count(1), Lua source string length.
    size = container[5]
    offset = 6
    if size == 255:
        size = struct.unpack_from("<Q", container, offset)[0]
        offset += 8
    if size <= 1 or offset + size - 1 > len(container):
        raise ValueError("original Lua bundle has no valid chunk name")
    return container[offset:offset + size - 1]


def compile_bundle(original: bytes, source: bytes) -> tuple[bytes, dict]:
    import UnityPy

    original_info = unityfs.parse(original)
    if len(original_info["nodes"]) != 1 or original_info["nodes"][0][0] != 0:
        raise ValueError("only the observed single-node Lua bundles are supported")
    _, _, original_plain, key = unityfs.payload_plain(bytearray(original_info["data"]))
    chunkname = original_chunkname(original_plain)
    standard = compile_source(source, chunkname)
    fci = codec.standard_to_fci(standard)
    if codec.fci_to_standard(fci) != standard:
        raise ValueError("compiled FCI bytecode failed exact round-trip")
    payload = codec.encode_fci(fci, key)
    env = UnityPy.load(original)
    text_assets = [obj for obj in env.objects if obj.type.name == "TextAsset"]
    if len(text_assets) != 1:
        raise ValueError("expected one Lua TextAsset in the original bundle")
    obj = text_assets[0]
    tree = obj.read_typetree()
    tree["m_Script"] = payload.decode("utf-8", "surrogateescape")
    obj.save_typetree(tree)
    resized = unityfs.parse(env.file.save())["data"]
    info = dict(original_info)
    offset, _, flags, name = info["nodes"][0]
    info["nodes"] = [(offset, len(resized), flags, name)]
    output = unityfs.build_uncompressed(info, resized)
    checked = unityfs.parse(output)
    _, _, checked_plain, _ = unityfs.payload_plain(bytearray(checked["data"]))
    checked_standard = codec.fci_to_standard(checked_plain)
    if checked_standard != standard or checked["nodes"] != info["nodes"]:
        raise ValueError("bundle verification failed after serialization")
    verify_lua(checked_standard)
    return output, {
        "chunkName": chunkname.decode("utf-8"),
        "originalSha256": sha256(original),
        "sourceSha256": sha256(source),
        "bundleSha256": sha256(output),
        "bundleLength": len(output),
        "bytecodeSha256": sha256(standard),
        "fciRoundTrip": True,
        "luaLoadable": True,
    }


def build_zip(entries: dict[str, bytes]) -> tuple[bytes, int]:
    """Deterministic ZIP with the game's nonnegative int32 CRC convention."""
    if not entries:
        raise ValueError("a patch must contain at least one real module")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, content in sorted(entries.items()):
            info = zipfile.ZipInfo(relative_path(path), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            archive.writestr(info, content, compresslevel=9)
        archive.comment = b"\0" * 4
    raw = bytearray(buffer.getvalue())
    if raw[-4:] != b"\0" * 4:
        raise ValueError("ZIP comment was not written as expected")
    for candidate in range(1 << 20):
        raw[-4:] = struct.pack("<I", candidate)
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        if crc < 0x80000000:
            with zipfile.ZipFile(io.BytesIO(raw)) as checked:
                if checked.testzip() is not None or set(checked.namelist()) != set(entries):
                    raise ValueError("ZIP integrity check failed")
                for path, content in entries.items():
                    if checked.read(path) != content:
                        raise ValueError("ZIP content changed")
            return bytes(raw), crc
    raise ValueError("could not encode the patch checksum as a positive int32")


def build_candidate(config_path: Path, bundle_root: Path, output_dir: Path,
                    base_url: str, zip_name: str = "ljjc-hot-update-v7.zip") -> dict:
    config_path, bundle_root, output_dir = config_path.resolve(), bundle_root.resolve(), output_dir.resolve()
    if relative_path(zip_name) != zip_name or "/" in zip_name or not zip_name.endswith(".zip"):
        raise ValueError("ZIP name must be a .zip filename")
    if not base_url.startswith(("http://", "https://")) or "?" in base_url or "#" in base_url:
        raise ValueError("base URL must be an HTTP(S) directory URL")
    source_root = config_path.parent
    if output_dir.is_relative_to(source_root) or output_dir.is_relative_to(bundle_root):
        raise ValueError("candidate output must be outside the input trees")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    repository = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository))
    from server.core.patch_versions import parse_version, select_patch_manifest

    parse_version(config["appVersion"])
    patch_version = config["patchVersion"]
    if type(patch_version) is not int or not 0 < patch_version <= 2_147_483_647:
        raise ValueError("patchVersion must be a positive int32")
    entries: dict[str, bytes] = {}
    records = []
    for module in config["modules"]:
        target = relative_path(module["target"])
        if not target.startswith("assetbundle/data/lua/") or target in entries:
            raise ValueError("module target must be a unique Lua bundle path")
        source_path = (source_root / relative_path(module["source"])).resolve()
        original_path = (bundle_root / target).resolve()
        if not source_path.is_relative_to(source_root) or not original_path.is_relative_to(bundle_root):
            raise ValueError("module path escaped its input tree")
        original, source = original_path.read_bytes(), source_path.read_bytes()
        expected_hash = module.get("originalSha256")
        if not isinstance(expected_hash, str) or sha256(original) != expected_hash:
            raise ValueError(f"original bundle digest mismatch: {target}")
        bundle, record = compile_bundle(original, source)
        if original_path.read_bytes() != original or source_path.read_bytes() != source:
            raise ValueError("input changed during candidate compilation")
        entries[target] = bundle
        records.append({"source": module["source"], "target": target, **record})
    raw, crc = build_zip(entries)
    manifest = {"version": config["appVersion"], "history": [{
        "version": patch_version, "length": len(raw), "checksum": crc,
        "urls": [base_url.rstrip("/") + "/" + zip_name],
    }]}
    select_patch_manifest(manifest, None)  # validate the actual client contract
    report = {"status": "offline-candidate", "published": False,
              "appVersion": config["appVersion"], "patchVersion": patch_version,
              "zip": zip_name, "zipLength": len(raw), "zipCrc32": crc,
              "zipSha256": sha256(raw), "modules": records,
              "validation": {"zipIntegrity": True, "fciRoundTrip": True,
                             "luaLoadable": True, "clientColdStart": "not-run"}}
    output_dir.mkdir(parents=True, exist_ok=True)
    for target, content in entries.items():
        destination = output_dir / "bundles" / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (output_dir / zip_name).write_bytes(raw)
    for filename, value in (("patch-manifest.json", manifest), ("build-report.json", report)):
        (output_dir / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("modules.json"))
    parser.add_argument("--bundle-root", type=Path, required=True, help="original decoded assets/Patch directory")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:19080")
    parser.add_argument("--zip-name", default="ljjc-hot-update-v7.zip")
    args = parser.parse_args()
    report = build_candidate(args.config, args.bundle_root, args.output_dir, args.base_url, args.zip_name)
    print(json.dumps({key: report[key] for key in ("status", "published", "zip", "zipLength", "zipCrc32", "zipSha256")}, indent=2))


if __name__ == "__main__":
    main()
