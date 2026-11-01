"""Recover the original ChatRoom Lua source for A10 contract verification.

This module contains a long string. FCI stores its extended length as uint32,
while stock Lua 5.3/unluac expects size_t (uint64 with our canonical header).
Expand that length only in a temporary input; never alter the APK dump.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile


def main():
    repo = Path(__file__).resolve().parents[1]
    workspace = repo.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--input", type=Path, default=workspace / "analysis/lua/window__chatroom__chatroom.luac")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("chatroom_codec", repo / "client-patch/hot-update/fci_lua_codec.py")
    codec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec)
    source = args.input.read_bytes()
    if not source.startswith(b"\x00fci\x01"):
        raise ValueError("expected the original runtime FCI Lua dump")
    long_offsets = []

    class FciReader(codec._R):
        def string(self):
            length = self.byte()
            if length == 255:
                long_offsets.append(self.p)
                length = self.u32()
            if length:
                self.skip(length - 1)

    canonical = bytearray(codec.LUA53_HEADER + source[4:])
    reader = FciReader(canonical, len(codec.LUA53_HEADER))
    reader.byte()  # main closure upvalue count
    spans = []
    codec._parse_function(reader, spans)
    if reader.p != len(canonical):
        raise ValueError("FCI parser did not consume the complete module")
    for offset in reversed(long_offsets):
        canonical[offset:offset + 4] = struct.pack("<Q", struct.unpack_from("<I", canonical, offset)[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ljjc-chatroom-") as temporary:
        patched = Path(temporary) / "chatroom.luac"
        patched.write_bytes(canonical)
        subprocess.run([
            str(workspace / "runtime/jdk21/jdk-21.0.12.1+1/bin/java.exe"), "-cp",
            str(workspace / "analysis/offline-project/lua-bytecode/toolchain/unluac-classes"),
            "unluac.Main", "--opmap", str(workspace / "runtime/ljjc-lua/fci-lua53.opmap"),
            "--output", str(args.output.resolve()), str(patched),
        ], check=True)
    print(f"source_sha256={hashlib.sha256(source).hexdigest()}")
    print(f"output_sha256={hashlib.sha256(args.output.read_bytes()).hexdigest()}")
    print(f"functions={len(spans)} extended_strings={len(long_offsets)} output={args.output}")


if __name__ == "__main__":
    main()
