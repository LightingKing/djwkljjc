# 《电击文库：零境交错》本地离线服务

这是一个只连接本机/局域网的个人离线实现。原版 APK 作为只读输入；服务端不连接官方账号、支付、推送或远程接口，支付只接受本地沙盒订单。

当前工作区按 [ROADMAP.md](ROADMAP.md) 分阶段实施。已经落地的基础能力包括：

- 游客账号、用户名密码账号、游客绑定、会话过期和 scrypt 加盐密码存储；
- 引导、目录、账号、主城初始化、教程、2011/2012/2013 剧情和章节 1 普通关卡的本地协议；
- 活动、签到、每日签到、连续登录、抽卡、角色/装备、任务、邮件、好友、公会、异步 PVP 的本地沙盒接口；
- M4.2–M4.6 的角色、装备、养成、道具和抽卡事务子集；M5.5 的金币本、爬塔、迷宫和锦标赛 PvE；M6 的固定种子机器人、聊天、助战、异步 PVP 和排行榜；
- 商城、Mall、月卡/通行证/礼包等本地充值周边；
- `local-sandbox:` 一次性支付下单和发货，远程 Apple/Google/Midas 回执一律拒绝；
- M3 基础设施：统一 SQLite 迁移底座、WAL、备份/恢复、资源账本、可注入服务器时钟、契约路由注册和覆盖率统计；
- M7 运维：启动备份、每日幂等备份、活动排期管理、按天审计日志和本地崩溃日志归档；
- 客户端 APK 清单、配置抽取、接口契约和可重复的补丁流水线；目录/版本控制面、状态探针、账号兼容入口和部分玩家接口已注册 68/84 个本地 sandbox 契约。

部分功能仍是明确标记的本地兼容实现，不代表官方服务端已被完整反推出。当前契约表有 84 个观察到的描述符接口；后续章节、完整掉落/养成数值、实时 PVP、全部原版活动和完整腾讯组件替换仍在 ROADMAP 后续里程碑中。配置表里 `observedCount > rowCount` 的文件是部分运行时样本，不能当作全量配置。

## 启动本地服务

推荐直接执行 `start-server.cmd`。它会在 `runtime/` 下创建最小引导目录、统一 SQLite 存档、备份目录和审计日志，并在没有证书时以本地 HTTP 模式启动：

```powershell
.\start-server.cmd
```

需要手动启动或接入已有 fixture 时，执行：

```powershell
python .\server.py `
  --config .\server.toml `
  --fixture-root .\fixtures `
  --log .\requests.jsonl `
  --cert .\certs\localhost.crt `
  --key .\certs\localhost.key
```

默认 HTTP 是 `127.0.0.1:19080`，TLS 是 `127.0.0.1:19102`。一键启动脚本固定使用回环 HTTP，并会拒绝非回环 `bind_host`，避免把账号 token 和游戏请求明文暴露到局域网。手机联调时，需手动运行 `server.py` 并提供证书/私钥，再把 `bind_host` 和 `public_base_url` 改成电脑专用网络地址；引导和目录响应会使用同一地址。`time_offset_seconds` 可用于本地时间测试。默认服务启动时使用 `state/local.sqlite3` 作为账号、玩家和经济共用的 WAL 数据库，并自动保留启动备份。

健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:19080/__local__/health
```

本机管理面板位于 `http://127.0.0.1:19080/__local__/admin`，账号维护、时钟偏移、备份/回档、活动排期、崩溃日志和推送会话诊断见 [docs/管理面板与备份.md](docs/管理面板与备份.md)。这些接口只接受回环请求。

服务端监听、TLS、请求审计和未知路由测试都在 `tests/test_server.py`。`server/__init__.py` 保留了旧的 `from server import FixtureServer` 兼容导出，新代码放在 `server/core/`。

## 客户端和工具

- [docs/客户端结构.md](docs/客户端结构.md)：MSDK、Midas、TP、TPNS、Bugly、GCloudVoice、Beacon、Tmsdk 和通信通道取证边界；
- [docs/APK静态分析-2026-10-28.md](docs/APK静态分析-2026-10-28.md)：有效 APK 的只读校验、清单、ABI 和外联组件分析；扫描工具为 [tools/apk_static_scan.py](tools/apk_static_scan.py)。
- [client-patch/README.md](client-patch/README.md)：SHA-256 校验、manifest 补丁、apktool、zipalign、apksigner 和 dry-run；
- [docs/APK清单.md](docs/APK清单.md)、[docs/配置抽取.md](docs/配置抽取.md)、[contracts/README.md](contracts/README.md)：数据底座说明；
- [docs/内容清单.md](docs/内容清单.md)：M2.8 内容上限和配置行与 APK 资源证据审计；可用 `tools/audit_content_coverage.py` 重新生成 `data/content-audit.json`；
- [docs/Protobuf描述符审计.md](docs/Protobuf描述符审计.md)：M2.6 描述符与契约逐类型审计，生成物为 `data/descriptor-audit.json`；
- [docs/统一存储与账本.md](docs/统一存储与账本.md)、[docs/时钟与重置.md](docs/时钟与重置.md)、[docs/路由与契约覆盖.md](docs/路由与契约覆盖.md)：M3 基础设施说明。
- [docs/本地证据审计.md](docs/本地证据审计.md)：区分本地处理器覆盖率与未注册路由安全探针，生成 `analysis/local-evidence-audit.json`。
- [docs/本地玩法烟测.md](docs/本地玩法烟测.md)：跨模块验证本地 M4–M6 兼容子集、幂等和重启持久化，脚本为 `dev/local_playability_smoke.py`。
- [docs/M2.5协议面审计.md](docs/M2.5协议面审计.md)：分开统计 IL2CPP 类型名、HTTP 描述符和运行时 WebSocket 证据；不会把发现数量当作实现覆盖率。
- [docs/M4角色装备道具.md](docs/M4角色装备道具.md)、[docs/M6机器人社交PVP.md](docs/M6机器人社交PVP.md)：本地玩法子集说明；[docs/功能验收清单.md](docs/功能验收清单.md) 是 M8.1 接口逐项验收入口。
- [docs/ROADMAP验收矩阵.md](docs/ROADMAP验收矩阵.md)：按证据区分已验证、部分实现、客户端阻塞和待审阅项目；可用 `python tools/audit_roadmap.py` 重新生成。
- [docs/本地活动页面.md](docs/本地活动页面.md)：M5.7 的离线活动排期/WebView 页面。
- [docs/M5网页与小游戏沙盒.md](docs/M5网页与小游戏沙盒.md)：公告状态、网页投票、分享和小游戏的本地持久化边界。

当前 Android 11 x86 模拟器存在系统 `MediaPlayer/libmedia.so` 原生闪退，详见 [docs/客户端稳定性.md](docs/客户端稳定性.md)。API28 x86 补丁冷启动已确认被 TPShell 完整性链路阻断；诊断脚本不会把闪退伪装成登录或战斗成功。未执行真实 APK 回包时，不要覆盖原 APK。

## 验证

```powershell
python -m unittest discover -s .\tests -q
```

当前测试覆盖账号、服务端 HTTP/TLS、目录契约、剧情副本、活动、商城/沙盒充值、客户端补丁流水线、统一存储/账本、时钟、内容审计和契约路由。最近一次全量回归为 223 项 pytest 通过（含 56 个子测试）；运行中的本地服务契约覆盖率为 68/84，其余接口保留明确的 501 边界。首次 Git 提交按 ROADMAP 约定留待用户审阅前执行。
