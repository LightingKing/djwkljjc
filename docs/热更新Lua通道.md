# 热更新 Lua 通道映射(绕开壳改客户端)

日期:2026-10-28。目标:用游戏自己的热更新通道,让本地服务器下发重新编码的 Lua,实现客户端改动
(服务器地址重定向、账号密码登录、Lua 层处理),**不改 APK、不脱壳、不用 Frida**。依据是已解码的
858 个 Lua、bootstrap 静态分析和现有本地服务。

## 为什么可行

- FCI Lua 加密是**对称 XOR**(4 字节轮转 key,key 从密文头可推),已验证**解码/重新编码逐字节无损**
  (`tools/decode_fci_lua.py`)。所以可以改 Lua 再编码回原格式。
- Lua 装在 AssetBundle 里(`assets/Patch/assetbundle/data/lua/*`),UnityPy 能读改写 AssetBundle 的
  TextAsset,可据此产出"改过的 bundle"。
- 热更新是游戏的合法更新路径,只走 Lua/资源层,**不触发壳(libtprt)的 native 完整性校验**——这正是
  三条脱壳路都撞墙后要绕开的东西。

## 已映射的通道

1. **引导**:`GET /pr2new_djwk.json` → `GameDynamicConfig`(各服务器 URL:dirServer/versionCheck/
   notify/updateCheck 等)。本地服务已实现,地址指向本机。
2. **更新检查**:native `ScenePatch.ProcessPatchInfo`(RVA `0x113d8cf`)把 clientupdate 响应反序列化成
   `PatchVersionInfo`。字段证据见 [bootstrap-static/patch-version-disassembly.md](../../analysis/ljjc-local/bootstrap-static/patch-version-disassembly.md)。
   - `PatchVersionInfo { history: [ PatchVersionInfoEntry ] }`。
   - `PatchVersionInfoEntry { version, length, checksum, urls[] }`。
   - **`history = []` = 无更新**(当前本地服务的行为,所以直接进游戏)。
   - `PromptPatchDownload`(`0x113da9c`)读 `history[0].version`;**当 version==0 时取 `urls[0]`** 作为直接下载地址
     (`0x113dcdb..0x113dd0f`)。所以最小可触发补丁的响应 = 一条 history,`version=0`、`urls` 非空。
   - 该路径只用 entry 的 `version/length/checksum/urls`,不比对顶层 version 与 `GameVersionInfo`。
3. **下载 UI**:Lua `Window_Patch_Patch` 只驱动界面:native 下载时发 `RecvUpdateNotify(totalBytes)`
   → `m_hasHotfix=true`;点更新按钮 `OnButtonUpdate` → `m_startDowner()`(native 下载器);
   完成发 `DownLoadResult`;修复走 `CS.ScenePatch.CleanPatch()` + `GameGlobal.ResetGame()`。
   补丁的下载、校验、落盘、加载全在 native(`CS.ScenePatch` / `CS.GameGlobal` / 资源管理器)。

## 还没确定(下一步要测的)

1. **`urls[0]` 下载回来的内容格式**:是单个 `length` 字节、按 `checksum` 校验的文件;但它是一个
   补丁包(zip / 自定义 bundle 列表)还是单个 AssetBundle、下载后怎么解开,native 里还没逆清。
2. **`checksum` 算法**(entry.checksum 对下载内容的校验:MD5 / CRC / 其它)。
3. **落盘的 patch 目录 + 加载优先级**:patch 后的 bundle 写到哪个持久目录,资源管理器是否按它覆盖 APK 里的
   同名 bundle。当前设备 `/data/data/com.tencent.tmgp.fcicv/files` 只有 cache/code_cache/lib,因为从没成功下过补丁。

## 下一步:用"强制一次补丁"把剩下的问号测出来

有现成的 Frida 环境(`emulator-5560`、frida-server 17.24.3)。最快的确认办法:

1. 让本地服务的 clientupdate 返回一条 history:`version=0`、`urls=["http://<本机>/<测试补丁>"]`、
   先随便给 length/checksum。
2. 启动原版客户端,用 Frida 挂文件/网络相关调用(open/write/rename、下载回调)+ 看 logcat,观察:
   - 客户端到底向哪个 URL 发请求、请求什么;
   - 下载内容写到哪个目录、什么文件名/结构;
   - 用什么算法校验 `checksum`(故意给错值看它在哪一步、怎么报错)。
3. 由此定出:补丁包格式、checksum 算法、patch 目录与加载覆盖规则。
4. 然后:用 UnityPy 把某个 Lua bundle(如 `window/login/login`)的 TextAsset 换成"改过并重新编码"的 Lua,
   按测出的格式打成补丁,本地服务通过该通道下发,验证客户端加载到改过的 Lua(先放个可见改动或日志确认)。
5. 跑通后,客户端改动(服务器重定向、账号密码登录等)都按这条通道做,手机端同样适用。

## 强制补丁测试结果(2026-10-28)

环境:本地 HTTP 服务(`server.py --no-tls`,监听 19080,fixture-root=`analysis/ljjc-local/fixtures`),
`adb reverse tcp:19080→tcp:19080`,原版客户端(未改),`emulator-5560`。
为驱动补丁,给 `server.py` 的 clientupdate 分支加了 override:存在 `runtime/force-patch.json` 时,
把它当作 clientupdate 的 `PatchVersionInfo` 用 raw_json envelope 下发。
`runtime/force-patch.json` 内容:`history=[{version:0, length:1024, checksum:..., urls:["http://127.0.0.1:19080/testpatch.bin"]}]`,
测试补丁 `analysis/ljjc-local/fixtures/testpatch.bin`(1024 字节)。

**已确认(服务器请求日志 `runtime/force-patch-requests.jsonl`)**:

- 客户端请求 `GET /clientupdate/` 拿到强制 history 后,**自动下载 `urls[0]`**:连续 `GET /testpatch.bin`。
  **不需要点更新按钮**,补丁检查在启动早期自动进行(与权限对话框并行)。
- 下载回来后**会校验 checksum**:给错的 checksum(全 0)时,客户端**重试 3 次**(`GET /testpatch.bin` ×3),
  与 Lua `Window_Patch_Patch` 的 `m_retryTimes = 3` 一致 → 证明下载后有校验、失败即重试。
- `checksum` 字段**实为 int(CRC32),非 MD5**——详见下节完整逆向;之前的 MD5 猜测已被反汇编推翻。

**这证明:本地服务器能通过游戏自己的热更新通道让客户端下载内容——通道可用。** 这正是本测试要确认的。

**实测中的其它发现**:

- 首次启动有一个 app 自绘的权限对话框(Location/Phone/Files/Microphone + Continue),会挡住主流程;
  补丁检查在它之前/并行跑。`pm grant` 或点 Continue(坐标约 1219,612)可放行。
- 客户端还有**另一条独立更新通道** `oth.update.mdt.qq.com:8080`(腾讯 MDT,会超时 20s),与 clientupdate 不同。
- 同一版本补丁校验失败后,重启不一定再次下载(疑似按版本缓存了"已检查/失败"),要重新触发可能需改 entry 或清补丁缓存。

**还差最后两项(下一步)**:

1. **确认 checksum = MD5**:把 `force-patch.json` 的 checksum 填成 `testpatch.bin` 的真实 MD5
   (`b2ea9f7fcea831a4a63b213f41a8855b`),让一次全新的补丁检查通过;通过(不再重试、继续)即坐实 MD5。
2. **补丁包格式 + 落盘/加载**:让一个 checksum 正确的下载走到"应用补丁"那步,看它期望的包格式
   (单个 AssetBundle?zip?带文件清单?)、写到哪个目录、资源管理器是否按它覆盖 APK。
   可用 Frida 挂 `openat`/`rename`/hash 函数在一次全新检查时抓,或在 Ghidra 里看 `ScenePatch` 的 apply。

拿到格式后,就能用 UnityPy 把改过并重新编码的 Lua(如登录界面)打成补丁、本地服务下发、客户端加载验证。

## 结论

热更新通道已**实测可用**:强制 clientupdate 的 history 条目 → 客户端自动从本地服务器下载 `urls[0]` → 校验 checksum(16字节/MD5)。
下发 Lua 的使能技术(FCI 无损重编码、UnityPy 改 bundle)已到位。只差确认 MD5 和补丁包格式这两项,即可下发改过的 Lua。
这条路不碰壳,是做客户端改动的正确方向。

当前环境保持可复现:本地服务 + reverse + `runtime/force-patch.json` + `emulator-5560` 都在,供下一步直接用。


## 补丁机制完整逆向(2026-10-28,libil2cpp 静态反汇编)

用 capstone 对 `libil2cpp.so`(x86)按 `script.json` 的符号反汇编了 `ScenePatch` / `ResourceManager` 的补丁相关方法,拿到完整机制。工具:`runtime/il2dis.py`、`runtime/il2find.py`。

### 清单(protobuf 字段,来自 dump.cs)

- `PatchVersionInfo { string version; repeated PatchVersionInfoEntry history }`
- `PatchVersionInfoEntry { int version; int length; int checksum; repeated string urls }`
- **`checksum` 是 int(32位整数),不是 hex 字符串**。这纠正了之前"可能是 MD5"的猜测——之前测试把 checksum 填成 32 位 hex 串,反序列化成 int 后根本不对,所以必然校验失败、重试 3 次。

### 下载 → 校验 → 解压(`ScenePatch.DownloadSinglePatch` / `VerifyChecksum` / `Unzip`)

1. 取 `entry.Urls[0]`,用 `Path.GetFileName(uri.LocalPath)` 和 `ResourceManager.PatchCachePath`(= `persistentDataPath/PatchCache`)拼出本地文件名,下载到 PatchCache。
2. `VerifyChecksum(path, entry.Checksum)`:用 **`ICSharpCode.SharpZipLib.Checksum.Crc32`** 算下载文件的 CRC32,和 `entry.Checksum`(int)比。**校验算法是 CRC32,不是 MD5。**
3. 校验通过 → `Unzip`:用 **`ICSharpCode.SharpZipLib.Zip.FastZip.ExtractZip`** 把下载文件解压。**所以补丁包是一个 ZIP。**

### 落盘与加载覆盖(`ResourceManager`)

- `InitSingleton`:`m_externalPatchPath = persistentDataPath/Patch`、`m_patchCachePath = persistentDataPath/PatchCache`(`Application.persistentDataPath` + "Patch"/"PatchCache")。常量 `PATCH_PATH="Patch"`、`PATCH_CACHE_PATH="PatchCache"`、`FOLDER_AB="AssetBundle"`。
- `DeduceRuntimeResourcePath(resName)`:**先 `File.Exists` 查 Patch 目录,再回退到 `NativeInterface.External_DoesAssetExist`(APK)**。→ **放进 `persistentDataPath/Patch` 的同名 bundle 会覆盖 APK 里的版本。这就是下发改过 Lua 的加载机制。**
- ZIP 解压进 Patch 目录,结构应与 APK 内 `assetbundle/...` 对应(FOLDER_AB="AssetBundle")。

### 二级校验:ValidatePatch(每次启动都跑)

- `ScenePatch.Init` 每次启动调用 `ValidatePatch`。它用 `CalculatePatchChecksum` 遍历 ExternalPatchPath 所有文件(排序)逐个 CRC32,再 BlockCopy 合成一个总 CRC32,和 `GameVersionInfo.PatchChecksum` 比;**不相等就 `CleanPatch`(清空 Patch 目录)**。
- `UpdatePatchMeta(version, checksum)` 在成功打补丁后设 `GameVersionInfo.PatchVersion` + `PatchChecksum` 并 `SaveVersionInfo`。
- **影响**:不能简单地 adb 手动丢一个 bundle 进 Patch 目录——下次启动 ValidatePatch 发现合成 CRC 和存档里的 PatchChecksum 不符,会把它清掉。必须走正规补丁流程(下发 zip、CRC32 正确),让 UpdatePatchMeta 写入匹配的 PatchChecksum;或同时改存档里的版本信息使之匹配。

### 交付改过 Lua 的完整路线(据此实现)

1. 用 UnityPy 把目标 bundle(如 `window/login/login`)的 TextAsset 换成"改过并 FCI 重新编码"的 Lua,保存 bundle。
2. 按 Patch 目录结构把改过的 bundle 打成 **ZIP**。
3. 算该 ZIP 的 **CRC32(int)**,填进 `force-patch.json` 的 `entry.checksum`;`urls` 指向本地服务上的该 zip;`length` = zip 字节数。
4. 处理 `PatchChecksum` 门:要么让 UpdatePatchMeta 在本次下载后写入与解压后 Patch 目录合成 CRC 匹配的值(跟随正规流程),要么据 ValidatePatch 的合成算法预先算好并写入存档。
5. 启动客户端 → 下载 → CRC32 校验 → FastZip 解压进 Patch → ValidatePatch → 加载改过的 Lua。先放个可见改动或日志确认命中。

### 还需实测确认的小点

- `UpdatePatchMeta` 的 `checksum` 入参具体取自清单的哪个字段(entry 还是顶层),以及 ValidatePatch 合成 CRC 的精确字节序/拼接方式——实现时用一次真实下载 + Frida 挂 `CalculatePatchChecksum` 的输入/输出对齐即可。

## 实测 Step1:投递 zip 补丁 + CRC32(2026-10-28)

- 构造最小测试 zip(`PATCH_MARKER.txt`,156 字节),刷 EOCD 注释使其 CRC32 高位为 0
  (`0x4298046e` = 正 int `1117258862`),规避 `entry.checksum` 作 int32 sign-extend 的坑。
- `runtime/force-patch.json`:`checksum=1117258862, length=156, urls=[http://127.0.0.1:19080/testpatch.zip]`。
- 本地 HTTP 服务(`--no-tls` 19080)+ `adb reverse 19080`。

**结果**:
- 服务器日志 `GET /testpatch.zip 200` **只下载 1 次**(失败会重试 3 次,见前面 .bin 测试)→ **CRC32 校验很可能通过**。
- 客户端在 `persistentDataPath`(`/sdcard/Android/data/com.tencent.tmgp.fcicv/files`)下**创建了 `Patch/` 和 `PatchCache/` 目录**(之前不存在),与反汇编出的 `ResourceManager.InitSingleton` 一致。
- 但两目录为空:本次没走到解压/应用。日志显示 `Unity: New version detected, clean previous patch`,随后
  `DoRequest failed: https://prds.djwk.qq.com:9102/pr2new_djwk.json`(6 次重试后 GetResponse failed)。

**诊断**:客户端的版本状态被重置("New version detected" → InitSingleton 比较
`GameVersionInfo.AppVersion`(存档)与 `External_GetAppVersion` 不符 → CleanPatch),于是它重新去**真实
bootstrap 地址** `prds.djwk.qq.com:9102` 拉配置。当前没有完整路由(guest `/system/etc/hosts` 把
`prds.djwk.qq.com` 指到 127.0.0.1 + `adb reverse 9102→19102` + 本地 TLS 服务 + CA 信任),所以 bootstrap 失败,
补丁流程进入半缓存的不稳定态(clientupdate 仍走缓存命中、zip 下到了,但没推进到解压)。

**下一步(要稳定端到端)**:
1. 架好完整 bootstrap 路由:guest hosts `prds.djwk.qq.com→127.0.0.1`、`adb reverse 9102→19102`、本地 TLS 服务
   (`--cert analysis/ljjc-local/certs/server.pem --key server-key.pem`)、CA 信任(原版 targetSdk=22,默认信任用户 CA)。
   这样 bootstrap 恒定命中本地服务,版本/配置状态稳定,补丁流程可复现。
2. 复跑 Step1,确认 zip 解压进 `Patch/`(拿到解压后的目录结构 = Patch 相对路径)。
3. 处理 ValidatePatch 的合成 CRC 门(让 UpdatePatchMeta 写入匹配的 PatchChecksum)。
4. Step2:把改过并 FCI 重编码的 login bundle 按该结构打 zip 投递,确认改过的 Lua 被加载。

环境保持:本地 HTTP 服务 + reverse + `runtime/force-patch.json`(当前指向 testpatch.zip)+ `emulator-5560` 均在。

## 根因确认:bootstrap 用 TLS 1.0(2026-10-28)

架好完整路由后(guest hosts `prds.djwk.qq.com→127.0.0.1` bind-mount、`adb reverse 9102→19102`、
`ljjc-cacerts` bind-mount 到系统 CA 库、本地 TLS 服务 19102),客户端**能连到**本地 TLS 监听,但握手必失败:
服务端日志 `tls_handshake status=error UNEXPECTED_EOF_WHILE_READING sni=None`,客户端
`DoRequest failed: https://prds.djwk.qq.com:9102/pr2new_djwk.json ... Server Unreachable`。

抓包确认根因(`analysis/ljjc-local/bootstrap-tls.pcap`):**游客端 ClientHello 是 TLS 1.0(record/hello 版本都是 3.1),且不带 SNI**。
本机 Python 3.12 的 OpenSSL 3.x **根本协商不了 TLS 1.0**(`NO_CIPHERS_AVAILABLE`,即使 `--legacy-tls` 设了 SECLEVEL=0/min=TLSv1 也不行,底层没有 TLS1.0 密码套件)。所以握手直接 EOF。

证书本身没问题(CN/SAN 含 `prds.djwk.qq.com`),服务端 TLS 1.2/1.3 对 Python 客户端正常。纯粹是**游戏要 TLS 1.0、现代 OpenSSL 给不了**。

**重要背景**:之前 launch-local.ps1 是用 **Frida 运行时注入**本地配置来**跳过 bootstrap**,所以没撞到这个 TLS 1.0 问题。本次走"无 Frida"路线才暴露出来。另外,clientupdate 和补丁下载本身都是 **HTTP**(已验证可用);只有最初的 bootstrap(pr2new)是 HTTPS。有缓存的 DynamicConfig 时,bootstrap 会被跳过,整条链路纯 HTTP——这正是更早几次运行能工作的原因。本次是我在 route-3 重签名实验里卸载/重装原版 APK **清空了应用数据**,把缓存的 DynamicConfig 删了,才逼出 bootstrap。

### 两条修复路(择一)

1. **上一个支持 TLS 1.0 的终止端**:在 19102 前面放一个能说 TLS 1.0 的 TLS 终止器(stunnel,或旧版 OpenSSL `s_server`,或用支持 TLS1.0 的库自写 socket 服务),把解密后的请求转发给本地 HTTP 服务。这样未改动的游戏能用 TLS 1.0 完成 bootstrap。最稳,新手机也适用。
2. **恢复/注入缓存的 DynamicConfig 跳过 bootstrap**:把指向 127.0.0.1 的 DynamicConfig 直接写回客户端的 PlayerPrefs(Unity → `/data/data/<pkg>/shared_prefs/<pkg>.v2.playerprefs.xml`),让它不再 bootstrap。之后 clientupdate + 补丁下载走纯 HTTP(已验证)。更简单,但要搞清 PlayerPrefs 里 config 的键名和序列化格式。

### 结论(本阶段)

热更新通道 + 补丁机制已**完全逆清**(ZIP 包、CRC32 int 校验、解压到 persistentDataPath/Patch、加载器先查 Patch 再查 APK、ValidatePatch 门),本地服务器**投递补丁下载已验证可用**(HTTP)。端到端"解压+加载改过的 Lua"目前卡在 **TLS 1.0 bootstrap**(我自己清空应用数据所致),按上面两条修复路之一即可打通。FCI 无损重编码 + UnityPy 改 bundle 已就绪,打通 bootstrap 后即可交付改过的 Lua。

## TLS 1.0 已解决,但撞上 Mono 证书信任墙(2026-10-28)

- 把 `server.py` 的 legacy-tls 密码改成 `ALL:@SECLEVEL=0`(原为 `DEFAULT:@SECLEVEL=0`,漏了游戏要的老套件)。
  现在服务端 19102 能接 **TLS 1.0**:`openssl s_client -tls1 -cipher 'AES256-SHA:@SECLEVEL=0'` 握手成功,
  协商出游戏 ClientHello 里排第一的 `RSA_AES256_SHA`。服务端证书是 RSA 2048(满足游戏只用 RSA 密钥交换)。
- 游戏 ClientHello(抓包):TLS 1.0,只给 **RSA 系**套件(AES256/128-SHA、3DES、RC4、一堆 export),无 ECDHE/GCM/SNI。
- **但游戏仍握手失败**:服务端日志 `UNEXPECTED_EOF_WHILE_READING`——服务端发完证书后,游戏关连接。
  logcat 实锤:`Mono.Security.Protocol.Tls.RecordProtocol.InternalReceiveRecordCallback`,dump 也显示 bootstrap 走
  Mono `HttpWebRequest` + `SslStream`。**游戏用的是 Mono 自己的 TLS/证书库,不是 Android 系统库**,所以
  bind-mount 的 Android CA 没用,游戏不信任我们的自签 CA → 发完证书就断。

### 结论:无 Frida、无改 APK 的 bootstrap 被 Mono 证书信任挡死

- TLS 1.0 传输层已通;挡路的是 Mono 的证书校验。修它要么让 Mono 信任我们的 CA,要么跳过 bootstrap。
- 改 APK 这条也走不通:TPShell 加壳挡重打包(见 route-3)。
- clientupdate + 补丁下载本身是 HTTP,bootstrap 一旦过了,后面全程不需要再碰 TLS。

### 三个可行方向

1. **让 Mono 信任我们的 CA**(最干净,若可行):老 Mono 在 Android 上的受信根库在 app home 下
   `~/.config/.mono/certs/Trust/`(即 `/data/data/<pkg>/.config/.mono/certs/Trust/<hash>.0`)。用 adb(root)把我们的 CA
   放进去,Mono 的 `X509StoreManager.TrustedRoot` 可能就信了。要实测确认这个 Unity 构建的 Mono 读不读这个路径。
2. **注入缓存配置跳过 bootstrap**:游戏的 DynamicConfig **不在** Unity PlayerPrefs(只有 version code)。它缓存在哪
   还没定位(persistentDataPath 下某文件或 Mono isolated storage)。定位到就能用 adb 写入、跳过 bootstrap。
3. **Frida 仅用于一次性跳过 bootstrap**:launch-local 原本就是这么做的。用 Frida 只注入本地配置(不碰内容),
   之后 hot-update 内容交付全程 HTTP、无 Frida。属于混合方案。

补丁机制和内容交付(hot-update)那部分已经完全就绪;唯一的门槛就是让游戏"起步"(bootstrap)。方向 1 最值得先试。

## 端到端打通:本地服务器成功下发补丁并被客户端应用(2026-10-28)

把三处卡点全部解决后,未改动的原版客户端**完整走通了热更新流程**:

1. **Mono 证书信任**(方向 1 成功):把自签 CA 转成 DER,放进 app 的 Mono 受信库
   `/data/data/com.tencent.tmgp.fcicv/.config/.mono/certs/Trust/`(DER,名随意,Mono 读目录下所有证书)。
   之后游戏的 Mono TLS 1.0 握手**信任我们的证书**,bootstrap 成功(服务端日志 `tls_handshake ok` + `pr2new_djwk.json 200`)。
   (同时放了 files/.config 和 sdcard/.../files/.config 两处备选;生效的是 `/data/data/<pkg>/.config`。)
2. **TLS 1.0 密码**:`server.py` legacy-tls 密码改 `ALL:@SECLEVEL=0`(原 DEFAULT 漏了游戏要的 RSA 老套件)。
3. **clientupdate 必须是纯 JSON**:`ProcessPatchInfo` 的反序列化器按 protobuf-JSON 解析,不能用 `encode_raw_json`
   的 wireformat 信封(`0x88 0x01` 帧会被当成 JSON 里的 U+0001 报 `InvalidJsonException: Parse patch info failed`)。
   改成直接返回 `force-patch.json` 字节、`application/json`。
4. **entry.version 语义**:`version=0` = "版本过低→浏览器跳转下载整包"(VersionTooLow/JumpShareActivity 打开浏览器);
   **`version!=0`(用 1)= 增量补丁**:界面显示"检测到 156 B 资源需要更新 / 立即更新"。

点"立即更新"后,**完整链路成功**:
- `GET /testpatch.zip 200` **只下 1 次** → CRC32(int,SharpZipLib)校验通过;
- 下载落到 `persistentDataPath/PatchCache/testpatch.zip`;
- FastZip 解压 → `persistentDataPath/Patch/PATCH_MARKER.txt` **成功释出**;
- `ValidatePatch`(每次启动)**没有清掉**补丁(UpdatePatchMeta 自动写入匹配的 PatchChecksum,校验通过,补丁持久);
- 客户端**越过补丁界面,进入腾讯用户协议/登录流程**(post-patch.png)。

**这证明:未改动的原版客户端,能通过它自己的热更新通道,从本地服务器下载并应用我们下发的补丁包,且补丁持久、客户端继续运行。** 不改 APK、不脱壳、运行期无 Frida(Mono CA 用 adb 一次性写入,属环境准备而非运行注入)。

### 可复现的最小配置

- 路由:guest hosts `prds.djwk.qq.com→127.0.0.1`(bind-mount)、`adb reverse 19080→19080` 和 `9102→19102`、
  Mono 受信 CA 放 `/data/data/<pkg>/.config/.mono/certs/Trust/`。
- 服务:`server.py --legacy-tls`(ALL:@SECLEVEL=0,RSA 2048 证书 SAN 含 prds.djwk.qq.com)。
- `runtime/force-patch.json`:`history=[{version:1, length:<zip字节>, checksum:<zip的CRC32 int,高位置0避开符号>, urls:["http://127.0.0.1:19080/<patch>.zip"]}]`。
- 操作:启动 → 过权限(Continue)→ 补丁界面点"立即更新"。

### 最后一步:下发真正改过的 Lua

机制已 100% 打通,只差把 zip 里的占位文件换成**真正改过的 AssetBundle**:
1. 确认 lua bundle 在 Patch 目录下的相对路径(`DeduceRuntimeResourcePath` 先查 Patch;zip 内路径 = Patch 相对路径;
   占位文件是放在 zip 根 → 释出到 Patch 根,真 bundle 要放到加载器查的那个相对路径,对应 APK 内 `assetbundle/data/lua/...`)。
2. 用 UnityPy 改目标 bundle(如 `window/login/login`)的 TextAsset:FCI 解码 → 改 → FCI 重编码 → 存 bundle。
3. 按该相对路径打 zip,算 CRC32 填 `force-patch.json`,下发,确认加载到改过的 Lua(放可见改动/日志验证)。

## 真·改过的 Lua 下发:通道全通,但 bundle 覆盖未生效(2026-10-28)

用 UnityPy 把 `window/login/login` bundle 的 TextAsset:FCI 解码 → 把 `print("Account",token)` 的常量
`Account` 字节改成同长度标记 `LJJCHOT` → FCI 重编码 → 存回 bundle(往返校验:reload 解码后含 `LJJCHOT`)。
按 `assetbundle/data/lua/window/login/login` 打 zip,算 CRC32 填 `force-patch.json`(version=2 > 已存的 1 以重新触发)。

**下发与解压全部成功**:点"立即更新" → `GET /loginpatch.zip 200`(CRC32 过)→ FastZip 解压到
`persistentDataPath/Patch/assetbundle/data/lua/window/login/login`;版本号显示 **2.0.4.59162.2**(补丁已应用);
客户端进到登录界面,`local-guest-entry.lua`(Frida Lua 求值器 evaluate_client_lua.js)启用"游客登录"按钮,点它后
`EntryReg/Account/Auth` 全 200,游客登录成功。

**但验证表明加载的是 APK 原版,不是补丁 bundle**:
- `print` 在此 build 是空操作(直接 `print('X')` 也不进 logcat),所以标记看不到。
- 改用 Frida Lua 求值器做**字节码取证**:`string.dump` 登录模块的函数(从 `UIWindowManager:FindWindow('Login')`
  的 `LuaInjector.Sandbox` 取),检查字节码含 `LJJCHOT` 还是 `Account`。结果全部 `=Account` → **加载的是 APK 版,补丁 bundle 没被用**。

**lua 加载链(已逆清)**:`LuaManager.LuaLoader` → `String.Concat("Data/Lua/"+模块+".bytes")` →
`ResourceManager.GetBytes` → `GetTextAsset` → `GetResourceByType<T>` → ...。`DeduceRuntimeResourcePath` 虽有
"先查 Patch 目录 File.Exists 再回退 APK External_DoesAssetExist",但 hook 该函数在 lua 加载时**没命中**
(require 命中缓存,或 lua bundle 解析走 AssetBundleManifest 而非逐文件 File.Exists)。

**结论**:投递通道(下载/校验/解压/持久/继续)已 100% 打通;但**让游戏加载 Patch 里的 bundle 而不是 APK 原版**
还没成,原因待定(三选一,下一步排查):
1. **AssetBundleManifest**:bundle 解析走 manifest(APK 内),补丁必须**同时下发更新后的 manifest**(列出新 bundle 及其 hash),而不是只丢一个 bundle 文件。
2. **路径/命名**:Patch 下的相对路径或命名(大小写、.bytes、hash 名)与加载器期望的不完全一致。
3. **缓存**:登录 lua 在补丁应用前已加载并缓存,需确保补丁在加载前就位或清缓存。

**可复用的验证方法**:`string.dump` + Frida Lua 求值器,直接从运行中的游戏确认某模块加载的是不是补丁版(查字节码里的标记)。这是判断覆盖是否生效的权威手段。

## 实测 Step2-3:自建 UnityFS 写包 + 完整交付改过的 Lua —— 全链路打通(2026-10-28)

> 本节**推翻上一节的三条猜测**(manifest / 路径 / 缓存)。真正卡点是**测试方法的两个混淆因素**加 **UnityPy 写包不可用**,与 AssetBundleManifest 无关。`string.dump` 已显示 `OnAccountAuthResponse=LJJCHOT`,全链路跑通。

### 卡了很久的"AB_LOAD_FAILED / Unable to open archive file"根因(全是测试方法问题,不是包格式)
之前用 `adb push` bundle 到 Patch 路径再 `AssetBundle.LoadFromFile(path)` 测"能不能加载",一直失败。逐一证伪:
1. **SELinux 上下文 + 属主**:`adb push`(root)写出的文件 context 是 `u:object_r:storage_file:s0`、属主 `root:root`;
   而该目录(app 自己建)是 `u:object_r:media_rw_data_file:s0:c512,c768`、属主 `u0_a170:ext_data_rw`。
   沙箱里的 app 带 MLS 类别 `c512,c768`,**打不开缺这两个类别的文件** → 内核层 "Unable to open archive file"。
   **连原版未改 bundle push 过去也报同样错** → 证明和包格式无关。
   修复:`su 0 chown u0_a170:ext_data_rw <f>; su 0 chcon u:object_r:media_rw_data_file:s0:c512,c768 <f>`。
   修好上下文后,改名 CAB 的原版 data 立刻 `LoadFromFile` 成功(返回非 nil)。
2. **同名 CAB 冲突**:登录界面时 APK 的 login bundle(CAB `96b71add…`)已加载;再 `LoadFromFile` 一个**同 CAB** 的文件,
   Unity 明确报 "can't be loaded because another AssetBundle with the same files is already loaded" → nil。
   **冷启动时加载器先于 APK 从 Patch 加载,无此冲突**,所以不是真正的阻碍。
3. 结论:`LoadFromFile` 测试法本身被上述两点污染;真正判据是**走正规通道冷启动后用 `string.dump` 取证**。

### UnityPy 写包不可用 → 自建 UnityFS v6 写包器
- UnityPy 重打的 bundle(含纯 round-trip)Unity 5.6.6 **开不了档**("Unable to open archive file" 是外层 UnityFS 解析失败,不是内部 SerializedFile)。
- 原版 `login` 结构:UnityFS v6,`5.6.6f2`,flags `0x43`(blocksInfo 合并+LZ4HC),单 block(uncomp 56880 / comp 41664),单 node CAB `96b71add…`,data 56880B。
- 自建写包器 **`runtime/patch_login_bundle.py`**:解压原 bundle 的 block → 在解压出的 SerializedFile 里就地改 FCI 负载 → 重新按原结构发射 UnityFS(data 与原版解压字节**完全一致**,仅改动的字节不同)。两种输出都验证可被 Unity 5.6.6 加载:
  - `build_uncompressed`(flags `0x40`,block 不压)——57017B;
  - LZ4HC 变体(flags `0x43`,block flags `0x3`,`lz4.block.compress(mode='high_compression',store_size=False)`)——41780B,结构与原版同。

### 标记选择(长度保持,安全)
- FCI 明文(luac)里**只有一处独立的 `Account` 字符串常量**(tag `0x04`+len `0x08`):即 `OnAccountAuthResponse` 里的 `print("Account", response.tokenAccess)`,纯调试打印,改成 `print("LJJCHOT", …)` 安全且长度不变(7→7)。
- 其余 86 处 `Account` 都是 `OnAccountRegResponse` 等**标识符子串**,不能动(会毁方法名)。
- `patch_login_bundle.py <orig> <out> marker` 只换这一处。

### ValidatePatch / PatchChecksum 门(为什么裸 push 一定被清)
- `ScenePatch.Init → ValidatePatch`:对 ExternalPatchPath 全部文件(排序)逐个 CRC32 再合成总 CRC32,与存档 `GameVersionInfo.PatchChecksum` 比,不等就 `CleanPatch` 清空 Patch 目录。
- 裸 `adb push` 的文件未登记 → 合成 CRC 不匹配 → **启动即被删**(实测 Patch 树清空)。
- 只能走正规通道:下载 zip → CRC32 校验 → FastZip 解压(app 写,上下文正确)→ `UpdatePatchMeta` 写入匹配的 PatchVersion/PatchChecksum,ValidatePatch 才留。

### ✅ 完整可复现交付配方(已实测跑通)
1. 改包:`python -I runtime/patch_login_bundle.py <原版login> runtime/loginpatch/login_lz4hc marker`
2. 打 zip(**Patch 相对路径**,放进 fixture-root `analysis/ljjc-local/fixtures/`):
   zip 内条目 = `assetbundle/data/lua/window/login/login`,内容 = 上一步的 bundle。
3. **CRC32 高位清零**:给 zip 的 EOCD 注释做 4 字节暴力,使整文件 `zlib.crc32` < `0x80000000`(否则 protobuf-JSON 的 int `checksum` 变负、校验必败)。
4. `runtime/force-patch.json`:`{"version":"2.0.4.59162.0","history":[{"version":N,"length":<zip字节>,"checksum":<zip CRC32 正整数>,"urls":["http://127.0.0.1:19080/<名>.zip"]}]}`
   - **`version` 必须 ≥1 且 > 存档 PatchVersion**(`version:0` 走的是"版本过低→前往下载"的整包/浏览器路径,不是增量)。
   - `version:1` 实测走**应用内增量**:界面显示"检测到 35.15 KB 资源需要更新"+"立即更新"。
5. 需要重新触发时,重置存档:`su 0 rm -rf …/files/Patch/* …/files/PatchCache/* /data/data/<pkg>/files/GameVersionInfo`(`GameVersionInfo` 本身也是 FCI 加密,已备份 `runtime/GameVersionInfo.bin`)。
6. 冷启动序列:系统"为旧版 Android 构建"对话框(点 OK,坐标 ~965,400)→ 标题/协议页(点屏幕继续)→ 补丁提示点"立即更新"(~1120,553)→ 下载解压应用 → 版本号变 `…59162.1` → 进登录页。
7. 验证:`runtime/checkmarker.lua`(经 `evaluate_client_lua.js` 求值)`string.dump` 登录函数查 `LJJCHOT`。

### 结果
- 服务器日志:`GET /loginpatch_marked.zip 200`(一次,无重试 → CRC32 正确);随后正常 `pr2new/DirAccount/ClickGame`。
- 版本号从 `2.0.4.59162.0` → `2.0.4.59162.1`(补丁已应用、PatchVersion 自增)。
- **`checkmarker` 返回 `OnAccountAuthResponse=LJJCHOT`,全库扫描 `LJJCHOT_hits=1`(正是改动的那一个函数),其余仍 `Account`。**
- **即:游戏通过自己的热更新通道加载并执行了我改过的登录 Lua。热更新 Lua 交付通道全链路打通。**

### 产物
- `runtime/patch_login_bundle.py`(自建 UnityFS v6 写包器 + FCI 改负载)
- `runtime/loginpatch/login_lz4hc`(改过的 bundle,41780B)、`analysis/ljjc-local/fixtures/loginpatch_marked.zip`(35992B,CRC32 `0x7415d043`)
- `runtime/checkmarker.lua`(字节码取证验证器,含全库扫描兜底)
- `runtime/force-patch.json`(交付清单,version=1)、`runtime/GameVersionInfo.bin`(存档备份)

## Lua 编译/回包工具链 —— 任意逻辑改动,全链路打通(2026-10-28)

> 此前的交付只能做**长度保持的字节替换**(标记测试)。要真正改登录等**逻辑**,需要把改过的 Lua **源码重新编译**成游戏能加载的 FCI bytecode。本节记录这套工具链,并已实测:重编译的 `login.lua`(整模块)被游戏正常加载运行,源码里的改动(`print("Account")`→`print("LJJCHOT")`)也生效。

### FCI bytecode 格式(两层)
- **第 1 层(TextAsset 加密)**:`m_Script = b"FCI\0" + XOR密文`;`decode_fci/encode_fci`(位旋转 4 字节密钥,密钥可从首 4 字节还原,编码时任选密钥即可)。
- **第 2 层(luac 容器)**:明文 = `00 66 63 69`(4 字节,**替代** Lua 标准 33 字节头)+ 标准 Lua 5.3 body(从 `sizeupvalues` 字节起)+ **每条指令 opcode 按 `fci-lua53.opmap` 置换**。
- **opcode 置换是纯轮转**:`FCI = (标准 + 17) % 47`(由 opmap 推导并校验)。指令是 32 位,opcode 在低 6 位。

### 编译器
- **lupa 的 `lua53`**(`runtime/.../lupa/lua53.cp312-win_amd64.pyd`,Lua 5.3)即编译器:`string.dump(load(src), false)` 产出标准 luac。其头部与 `LUA53_HEADER`(`04 08 04 08 08` = int4/size_t8/instr4/int64/double,小端,LUAC_INT=0x5678,LUAC_NUM=370.5)**逐字节一致**,无需自建或下载 luac。

### 工具
- `runtime/fci_lua_codec.py` —— FCI↔标准 luac 编解码。含 Lua 5.3 bytecode 解析器(递归遍历 proto 树定位所有 code 数组)、`fci_to_standard` / `standard_to_fci`、`decode_fci`/`encode_fci`、opcode 置换表。
- `runtime/build_lua_bundle.py` —— 源码→lupa 编译→FCI→**重打 bundle**。bundle 内层 SerializedFile(改 TextAsset、**变长**)用 UnityPy 重序列化,再用 `patch_login_bundle.build_uncompressed`(自建 UnityFS 写包器,node size 取新数据长度)包外层,避开 UnityPy 外层写包的坑。
- `runtime/deliver_patch.py` —— 打 Patch 相对路径 zip(**支持多文件**,为 M1.8 的 login+auth 同包交付准备)、刷 EOCD 注释使 CRC32 高位清零、写 `force-patch.json`。

### 验证门(都已通过)
1. **离线 opcode 往返(byte-exact)**:对 `analysis/lua/*.luac` 全量 858 个模块做 `fci→standard→fci`,**854 个逐字节一致且标准 luac 可被 Lua `load`**。4 个失败都是 `xlsx__*`/`chatroom` 数据表(我的解析器边界,**非逻辑模块、非 M1.8 目标**);`login`/`auth`/`guestloginprompt`/`account` 四个目标模块都 byte-exact 且可加载。byte-exact 往返即证明 code 数组定位与 opcode 置换正确。
2. **重编译保真 + 源码改动生效(真机,一次跑通)**:改 `login.lua` 源码 `print("Account", …)`→`print("LJJCHOT", …)` → lupa 重编译整模块 → 工具链回包 → 走热更新通道(`GET /login_recompiled.zip` 一次、无重试=CRC 正确)→ **游戏正常进到登录界面**(`FindWindow('Login')` 成功、75 个函数被扫描,登录界面渲染正常)→ `checkmarker` 返回 `OnAccountAuthResponse=LJJCHOT`、`LJJCHOT_hits=1`。即:unluac 反编译的整模块源码能被 lupa 重编译、被游戏正常运行,逻辑改动随之生效。

### 结论
**现在可以对任意逻辑模块改源码→重编译→经通道交付→游戏运行。** 这是 M1.8(客户端账号密码登录)及后续一切客户端内容改动的基础能力。产物:`runtime/loginpatch/login_recompiled_marked`、`analysis/ljjc-local/fixtures/login_recompiled.zip`。
