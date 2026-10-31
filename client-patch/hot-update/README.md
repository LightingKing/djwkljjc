# Offline hot-update candidates

This pipeline produces a reviewable candidate for the original APK 2.0.4.
It never installs a client, contacts a service, or writes an online manifest.
Input paths and the output directory are explicit; input bundles are checked
against the known SHA-256 digests in `modules.json` before compilation.

Install the offline tooling listed in `requirements.txt`, then run from any
directory:

```powershell
python client-patch/hot-update/build.py `
  --bundle-root '<decoded APK>/assets/Patch' `
  --output-dir '<offline candidate directory>' `
  --base-url 'http://127.0.0.1:19080'
```

`--config` and `--zip-name` are also configurable. Outputs are compiled
bundles, `ljjc-hot-update-v7.zip`, `patch-manifest.json`, and
`build-report.json`. Copying the ZIP to the fixture directory and changing
the service's manifest are separate deployment actions owned by the caller.
No output is named `force-patch.json`.

The compiler preserves each original chunk name and FCI key. It uses Lua 5.3
from lupa, the original opcode map, UnityPy for the inner TextAsset, and the
proven UnityFS v6 writer for the outer bundle. Every emitted bundle is
decoded again and its complete Lua bytecode compared with the compiled
input. ZIP paths and contents are checked; ZIP metadata is deterministic;
the EOCD comment makes the whole-file CRC32 fit the client's signed int32
field. Length and checksum are calculated from the final ZIP bytes.

`source-origins.json` records the source snapshots and their original digests.
The login/auth/IAP sources retain the existing v4 local account and sandbox
payment behavior. v5 adds `socialEntry`, server-backed purchase activity
information, collection filtering, and the corrected power purchase check.
v6 includes all five v5 modules, fixes cached local authentication, sets both
account input limits to 64 characters, consumes local login errors, and adds
RestifizerManager to normalize saved activity acknowledgement map keys.
The purchase activity widget also applies product purchase counts to every
matching seasonal Mall row, including the visible card. PurchaseGiftItem and
PurchaseBuy reuse the original text for Mall 918/919 when 926/927 have no
localized text, falling back to the original Item 123003 name and description.
Existing translated text and the native purchase reward display are preserved.
Password cache renewal requires the matching server's `/Auth/` JSON contract:
`{entryType:"password", username:<saved username>, accessToken:<saved token>}`.
Guest cache renewal retains the original `ApiAuthInfo` wireformat request.
The source files here are the candidate build inputs; the old runtime source
files are not read during a build.

Run `tests/test_hot_update_patch.py` and `tests/test_client_patch_v6.py` with the same optional dependencies to
exercise the Lua branches, FCI conversion, ZIP construction, and pure version
selection policy. A successful offline build does not certify cold start,
Unity rendering, or a real account purchase. Those gates are listed in
`docs/客户端补丁-v6-验收.md`. The v5 acceptance record remains historical evidence.

The default `modules.json` and its eleven source modules remain the v7 build
inputs. The cumulative v8 candidate uses `modules-v8.json`, which adds
`sources/mazemain.lua` and `sources/dungeonbalancefailed.lua`. The MazeMain
one-line change indexes maze prize boxes by quality,
matching the lookup and sprite names, so repeated prizes merge their counts.
The failure-window title adds an AsyncPvp-specific branch that reuses the
native activity title key `WeekActivitiesType_Name_5`; it no longer asks for
the absent `DungeonName_4001`. The native success route uses
`DungeonBalanceBurstLink`, which does not look up dungeon names and is unchanged.
Build it explicitly without changing a v7 candidate or the online manifest:

```powershell
python client-patch/hot-update/build.py `
  --config client-patch/hot-update/modules-v8.json `
  --bundle-root '<decoded APK>/assets/Patch' `
  --output-dir '<new v8 offline candidate directory>' `
  --base-url 'http://127.0.0.1:19080' `
  --zip-name ljjc-hot-update-v8.zip
```

`source-origins-v8.json` records the added source's provenance; the unchanged
v7 modules retain `source-origins.json`. Run `tests/test_client_patch_v8.py`
for the native Lua reward display and cumulative patch selection contracts.
The native maze journey still requires separate emulator acceptance.
