# Client patch pipeline

This directory is the home for the deterministic APK patch pipeline described
by ROADMAP M1.2. The original APK is a read-only input; patched APKs and
keystores stay under ignored `work/`, `output/`, and `keystore/` directories.

Implemented stages are SHA-256 verification, apktool unpack, manifest-driven
file/text patches, rebuild, zipalign, signing, and signature verification.
Frida remains a development capture tool only and is not part of the normal
play path.

The source APK is always opened read-only. Start with a dry run:

```powershell
python .\client-patch\build.py `
  --input .\original.apk `
  --manifest .\client-patch\patches\empty-patch.example.json `
  --output-dir .\client-patch\output `
  --expected-sha256 <sha256> `
  --dry-run
```

The repository includes a real-digest empty manifest at
`client-patch/patches/ljjc-offline-2.0.4-dryrun.json`. A local build may use
the ignored `client-patch/work/tools/apktool.bat` and
`client-patch/keystore/ljjc-offline.jks` paths after the toolchain has been
created. The build report records the input and output digest and is the
reviewable proof that the source package was not overwritten.

The `ljjc-offline-sdkshim-2.0.4.json` manifest is an explicitly scoped
diagnostic patch for the MSDK initialization crash. It is not a production
offline client: the package still contains the original TPShell integrity
application, and the API28 launch remains blocked there. Do not treat the
shim as permission to remove or bypass that integrity layer.

For Android 14/15 installation policy, `ljjc-offline-target24-2.0.4.json`
is an optional metadata-only patch that changes the decoded `targetSdkVersion`
from 22 to 24. It does not alter the application class, native libraries, or
integrity code. The target24 build still requires the same signature and
runtime cold-start verification as every other patched APK; raising the target
SDK alone does not make the client playable.

For a real build, set the keystore password in an environment variable and
provide apktool, zipalign, apksigner, keystore, and alias paths. The pipeline
refuses missing tools, a digest mismatch, unsafe patch paths, or a missing
signing password; it never overwrites the input APK.

## 本地沙盒支付适配

`client/local-sandbox-iap.lua` 是一个运行时 Lua 适配文件，不是 APK 资源替换。
在 `local-guest-entry.lua` 选择 `http://127.0.0.1:19080` 且账号为游客时，它包装
Lua 层 `IAPHandler.Purchase`，显示本地确认框，然后依次调用
`/fci/purchasetouch/` 和 `/fci/purchasedelevery/`。发货请求只携带服务端返回的
`local-sandbox:` 交易令牌；真实平台回执、QQ/微信凭据和 Midas 同步不会被读取或发送。

该文件由 REA/Lua 运行时加载，当前 `build.py` 只支持安全的 `replace_file`/
`replace_text` 清单，不能把它伪装成受 FCI 保护的内置 Lua 资源。因此它已完成源代码
和静态契约验收，但没有写入 target24/sdkshim APK；`M1.10` 的客户端购买 UI 仍须在
可启动的、未被 TPShell 完整性链路阻断的客户端上单独回归。服务端沙盒下单、一次性
令牌和重复发货保护已经由 `tests/test_economy.py` 覆盖。

## 工件验收

`dev/verify-client-artifact.py` 对已经构建的 APK 生成可复核 JSON 证据：计算
SHA-256、运行 `apksigner verify --verbose`、读取包名/版本/target SDK，并可在
已经安装**同一 SHA-256 工件**的模拟器上执行非破坏性冷启动。它只使用
`am force-stop`、`monkey`、`pidof`、`dumpsys` 和 `logcat`，不会安装、卸载、清除
数据或修改客户端保护逻辑。

```powershell
python .\dev\verify-client-artifact.py `
  --apk .\client-patch\output\target24\ljjc-offline-target24-2.0.4-universal.apk `
  --output .\dev\target24-artifact-verification.json `
  --expected-sha256 8e63b130cb925adb2c690f64b6a2802d4c4e223f1bf9db843ca4635bf6d2639d `
  --expected-package com.tencent.tmgp.fcicv `
  --expected-target-sdk 24 `
  --sdk-root 'C:\Users\Administrator\AppData\Local\Android\Sdk'
```

需要做冷启动时追加 `--serial emulator-5562 --cold-start-runs 5`。脚本会先用
`pm path` 和设备端 `sha256sum` 确认安装包就是待验收工件；摘要不一致时会记录
阻塞原因并跳过启动，避免把原版或另一把签名的测试包误报成当前构建的结果。
