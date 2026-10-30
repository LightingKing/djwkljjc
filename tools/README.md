# Development tools

Scripts for APK inspection, Lua/fci extraction, descriptor analysis, and trace
replay live here. Tools should accept explicit input/output paths and must not
modify the original APK in place.

The M1.2 build entry point is `client-patch/build.py`. It is intentionally
kept under `client-patch/` because it owns the patch manifest and generated
APK layout; use `tools/verify-toolchain.ps1` before invoking a real build.

`contract_harness.py` is the M3.8 local contract smoke/replay runner. It
generates deterministic JSONL requests from `contracts/api.yaml`, dispatches
them through `server.core.routes.RouteRegistry`, and emits a machine-readable
report. It never makes network requests. See `contracts/README.md` for the
replay record format.

`audit_descriptors.py` is the M2.6 descriptor audit. It compares message and
enum references in the contract table with the recovered descriptor set and
records malformed/skipped descriptor files without fabricating fields. Pass
`--il2cpp-dump analysis/il2cpp/dump-v6.7.46-x86/dump.cs` to add a read-only
cross-check of generated `IMessage` classes and `*FieldNumber` constants;
those matches are labeled `static_candidate_only` and are never merged into
the descriptor set. The report also includes `descriptorFieldEvidence`, a
read-only comparison of field-number sets for matched messages; it can flag
descriptor drift without inferring missing types or fields.

`audit_content_coverage.py` is the M2.8 resource evidence audit. It compares
selected configuration rows with paths in `data/apk-inventory.json` and keeps
partial configuration samples marked partial.

`audit_server_values.py` is the M2.7 provenance audit. It checks that every
leaf in `data/server-only/*.yaml` carries source, confidence, and local-policy
metadata; it does not raise the historical confidence of a local rule.

`audit_lua_decompilation.py` is the M2.3 Lua evidence and batch-conversion
runner. It distinguishes verified runtime FCI dumps from protected `FCI\0`
asset payloads, records SHA-256/status per input, and invokes a supplied
single-file converter only for the verified runtime layout. It never executes
Lua or writes over an input file.

`inspect_il2cpp.py` is the M2.4 read-only structure audit. It validates the
metadata v23 header/table ranges and native ELF ABI probes, then records the
versioned Il2CppDumper outputs and Ghidra import status when those artifacts
exist beside the report. A Ghidra project whose automatic analysis timed out is
reported as `imported_analysis_timeout`; the tool never upgrades that to a
completed analysis or claims symbol renaming.
