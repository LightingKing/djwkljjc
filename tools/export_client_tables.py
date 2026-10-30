"""Export the original client's complete xlsx config tables to JSON.

The server's ``data/config`` copies were reconstructed from partial runtime
captures (e.g. PlayerLevelInitConfig had 6 of 99 rows, DungeonReward 3 of 683).
The APK itself ships every config table as an FCI-protected Lua module; once
decoded and decompiled (``analysis/lua-decompiled/xlsx__*.lua``) each module is
pure data: ``local raw = { ProtobufI("Type", {...}), ... }`` ending in
``return {raw, indexed}``.  This tool evaluates those modules in an isolated
Lua 5.3 runtime with ``ProtobufI`` stubbed to return its table, and writes the
rows as JSON with provenance (source path + SHA-256).  Proto3 default values
(0 / false / empty) are omitted by the client data exactly as they are here;
readers must treat a missing field as its default.

Usage:
  python tools/export_client_tables.py PlayerLevelInitConfig DungeonReward ChapterInfo
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import lupa.lua53 as lua53

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "analysis" / "lua-decompiled"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "client-tables"


def _to_py(value):
    if lua53.lua_type(value) != "table":
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return value
    keys = list(value.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [_to_py(value[i]) for i in range(1, len(keys) + 1)]
    if not keys:
        return []
    return {
        (k.decode("utf-8") if isinstance(k, bytes) else str(k)): _to_py(v)
        for k, v in value.items()
    }


def export_table(name: str) -> Path:
    source = SOURCE_DIR / f"xlsx__{name.lower()}.lua"
    text = source.read_bytes()
    runtime = lua53.LuaRuntime(unpack_returned_tuples=True)
    # Isolate the chunk: only ProtobufI is visible, no io/os/require.
    sandbox = runtime.eval("function() return {ProtobufI = function(_, t) return t end} end")()
    loader = runtime.eval("function(src, env) return load(src, '=client-table', 't', env) end")
    chunk = loader(text, sandbox)
    if chunk is None:
        raise RuntimeError(f"cannot load {source}")
    result = chunk()  # a single Lua table: {raw, indexed}
    rows = _to_py(result[1])
    if isinstance(rows, dict):
        rows = [rows[k] for k in sorted(rows, key=lambda x: int(x))]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.json"
    out.write_text(
        json.dumps(
            {
                "table": name,
                "source": str(source.relative_to(ROOT)).replace("\\", "/"),
                "sourceSha256": hashlib.sha256(text).hexdigest(),
                "provenance": "original APK 2.0.4 config module (FCI-decoded, unluac-decompiled), evaluated as data",
                "rowCount": len(rows),
                "rows": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tables", nargs="+")
    args = parser.parse_args()
    for name in args.tables:
        out = export_table(name)
        rows = json.loads(out.read_text(encoding="utf-8"))["rowCount"]
        print(f"{name}: {rows} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
