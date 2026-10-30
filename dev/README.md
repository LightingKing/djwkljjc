# Development environment

The development emulator and host-only scripts live here. The existing
`DJWK_LJJC_API30` AVD is retained for compatibility; ROADMAP M0.4 adds the
isolated `LJJC_Offline_API28` AVD for no-audio/client stability testing.

Generated emulator snapshots, APKs, and logs must stay outside versioned
source directories.

Create and start the isolated Android 9 emulator:

```powershell
.\dev\create-api28-avd.ps1
.\dev\start-api28-avd.ps1 -WaitForBoot
```

The create step can be retried after an interrupted SDK download. The start
step uses `-no-audio`, port `5562`, and never resets or deletes
`DJWK_LJJC_API30` / `emulator-5560`.

After installing a local test APK, run five non-destructive cold starts:

```powershell
.\dev\cold-start-check.ps1 -Serial emulator-5562 -PackageName com.tencent.tmgp.fcicv
```

The check uses only `am force-stop` and `monkey`; it does not clear app data or
uninstall packages. A JSON report is written beside the script by default.
Since schema version 2 the report also records the device ABI list, API level,
`ro.dalvik.vm.native.bridge`, host architecture, process liveness, and clean
exit log matches. These fields make a 32-bit translation or emulator
architecture limitation visible without changing the package or device.

The top-level `status` is `passed`, `failed`, or `unavailable`; `reason` names
the diagnostic boundary. If ADB is offline, the script writes an
`unavailable` report before preserving its historical non-zero exit.

For example, an ARM64 AVD on an x86_64 host can fail before ADB comes online;
that is an environment limitation and should be recorded separately from a
client crash. The existing script intentionally keeps its original non-zero
exit behavior for an offline device or a failed cold start.

For a patched APK, run the artifact checker before a cold start. It validates
the package metadata and signature, then (when requested) compares the APK
SHA-256 with the installed package before launching it:

```powershell
python .\dev\verify-client-artifact.py `
  --apk .\client-patch\output\target24\ljjc-offline-target24-2.0.4-universal.apk `
  --output .\dev\target24-cold-start-api28.json `
  --expected-sha256 8e63b130cb925adb2c690f64b6a2802d4c4e223f1bf9db843ca4635bf6d2639d `
  --expected-package com.tencent.tmgp.fcicv `
  --expected-target-sdk 24 `
  --sdk-root 'C:\Users\Administrator\AppData\Local\Android\Sdk' `
  --serial emulator-5562 --cold-start-runs 5
```

The checker refuses to install or replace a package. If the installed digest
does not match the artifact, the report marks cold start as skipped so logs
from a different local signature cannot be attributed to this build.
