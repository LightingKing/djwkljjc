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
bundles, `ljjc-hot-update-v6.zip`, `patch-manifest.json`, and
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
