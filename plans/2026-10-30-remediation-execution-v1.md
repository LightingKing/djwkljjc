# 零境交错整改：现状复核与执行方案

## Objective

依据 2026-10-30 的整改计划，对照当前源码、运行服务、请求日志和已有客户端取证，确定可执行批次、依赖、回滚边界与验收标准。优先稳定已可玩的流程，再补付费功能与机器人玩法。

本轮仅核查和编写方案；未修改业务代码、存档、补丁清单，未重启服务或操作模拟器，未重新运行业务测试。原计划中的执行指令与规则建议作为待评审内容，不视为本轮实施授权。

### 当前基线（2026-10-30 北京时间约 17:18）

| 项目 | 核查结果 | 执行含义 |
|---|---|---|
| 在线服务 | Python 进程 31068，13:33:28 启动；HTTP 19080、TLS 19102；健康检查正常，管理面显示 1 个推送会话 | 已有可用服务，部署必须保留当前启动参数 |
| 模拟器 | emulator/qemu 进程及 5560 端口存在 | 本轮未操作界面；进程存在不代表所有功能验收通过 |
| 实际存档 | I:/workspace/mobilegame/analysis/ljjc-local/state/local.sqlite3 | 默认启动脚本使用另一处 runtime/state，不能直接用默认脚本替换当前进程 |
| 备份 | 实际 state/backups 下最近一份为 20261030T053329Z，即北京时间 13:33:29；自动备份未报错 | 实施前仍需对当时最新状态做在线备份，并在隔离目录试恢复 |
| 配置 | 现进程未传 --config，管理面时钟偏移为 0 | 只改 server.toml 不保证上线生效；部署要明确配置载入和默认值 |
| 源码与进程 | mall.py 于 14:12:07 修改，晚于进程启动 | 运行行为可能不同于磁盘源码，不能直接把两者当成同一版本 |
| 补丁 | I:/workspace/mobilegame/runtime/force-patch.json 指向 v4，当前以 --force-patch 下发 | Lua 源码、构建器、v4 包、清单均须纳入回滚快照 |
| 时间与排期 | 管理面锚点仍是北京 14:00；28 天周期，46 类签到仅开 7 天 | 时间和签到问题仍在；当前实际存档目录未发现 activity-schedule.json 覆盖文件 |
| 请求与推送 | 当前日志截至采样共 152 条 HTTP 请求，均为 200；仍发 ScMail、ScRedPointData、ScPurchaseDelevery | HTTP 成功不能证明客户端协议正确，须验证消息名、编码和回包形状 |
| Git | 仓库没有提交，项目文件均未跟踪 | 目前没有可用的 Git 回滚基线；先做源码快照并建立受控基线 |
| 测试证据 | 修复记录记载 464 项通过；README 仍写 223，pytest 缓存为更早的 444 个节点且留有失败记录 | 这些历史数字不能证明当前源码全绿；实施开始时须重新取得隔离测试基线 |

当前服务状态来自本轮只读管理接口与进程信息。历史客户端进展见 [第六批修复记录](I:/workspace/mobilegame/local-server/ljjc/docs/等级与关卡解锁修复.md:94)；旧说明见 [README.md:75](I:/workspace/mobilegame/local-server/ljjc/README.md:75)。68/84 是特定契约注册表的覆盖率，不代表整个游戏完成度。

### 对原计划的关键修正

| 原计划事项 | 核查与修正 | 证据 |
|---|---|---|
| #1 新建时间基础设施 | 现有 ServerClock 已有时区、日周边界；reset.py 可作为薄业务策略层。UTC epoch 不变，时区与测试时间偏移分开；签到午夜、日常六点需要独立策略 | [server/core/clock.py:62](I:/workspace/mobilegame/local-server/ljjc/server/core/clock.py:62)、[server.py:1856](I:/workspace/mobilegame/local-server/ljjc/server.py:1856) |
| #1 迁移最多多重置一次 | 尚无代码保证。日期串、整数日桶、免费抽卡绝对时间、门票与限购标记需分别迁移并持久化迁移版本 | [systems.py:2355](I:/workspace/mobilegame/local-server/ljjc/systems.py:2355)、[server/core/activity_schedule.py:50](I:/workspace/mobilegame/local-server/ljjc/server/core/activity_schedule.py:50) |
| #2 先做在线推送 | BaseResChange 会重新拉取错误的基础资源回包，故 #4 必须先完成；调度器须先持久化结算，再推送 | [systems.py:2030](I:/workspace/mobilegame/local-server/ljjc/systems.py:2030)、[manager__pushservice.lua:551](I:/workspace/mobilegame/analysis/lua-decompiled/manager__pushservice.lua:551) |
| #2/#3 发推送类型即可 | ItemChange、AchievementDone、MedalChange 要携带正确参数；任务推送不能清掉特殊活动任务；HTTP/TLS 共享一个结算调度器 | [server.py:279](I:/workspace/mobilegame/local-server/ljjc/server.py:279)、[tasks.py:569](I:/workspace/mobilegame/local-server/ljjc/tasks.py:569) |
| #5 仅在 actors.add 补天赋 | 抽卡和发奖回包也漏天赋；有路径创建之后才随机品质。必须最终品质确定后生成天赋，再统一保存和完整序列化 | [actors.py:180](I:/workspace/mobilegame/local-server/ljjc/actors.py:180)、[systems.py:2371](I:/workspace/mobilegame/local-server/ljjc/systems.py:2371)、[systems.py:3386](I:/workspace/mobilegame/local-server/ljjc/systems.py:3386)、[grants.py:71](I:/workspace/mobilegame/local-server/ljjc/grants.py:71) |
| #6 空 history 必然失败 | 项目内后续证据已纠正此结论：补齐前置链路后空 history 能继续。先验证当前 v4 的无更新响应，不猜版本比较逻辑、不先清缓存 | [patch-validation-disassembly.md:29](I:/workspace/mobilegame/analysis/ljjc-local/bootstrap-static/patch-validation-disassembly.md:29)、[同文档:108](I:/workspace/mobilegame/analysis/ljjc-local/bootstrap-static/patch-validation-disassembly.md:108) |
| #10 只改经济底层 | Mall 已显式维护 5/27 双写，下沉时须同步迁移调用方，否则重复加扣；此项应先于首充、月卡与付费养成 | [economy.py:1382](I:/workspace/mobilegame/local-server/ljjc/economy.py:1382)、[mall.py:361](I:/workspace/mobilegame/local-server/ljjc/mall.py:361) |
| #8 每次登录扣一天 | 与漏登作废存在矛盾：离线多天应按经过的结算日递减，只发当天合资格邮件。月卡天数是资源类型 24 | [mall.py:562](I:/workspace/mobilegame/local-server/ljjc/mall.py:562)、[PurchaseInfo.json](I:/workspace/mobilegame/local-server/ljjc/data/client-tables/PurchaseInfo.json) |
| 付费批次遗漏 | 限购在下单检查，交付未再次检查；多张待交付订单可能绕过限购，应在开放更多礼包前修复 | [mall.py:547](I:/workspace/mobilegame/local-server/ljjc/mall.py:547)、[mall.py:594](I:/workspace/mobilegame/local-server/ljjc/mall.py:594) |
| #18 未知任务白送金币 | 仍存在，属于资源一致性问题，提前到 P0 收尾；对已知 extra-activepoint 单独返回正确结果 | [systems.py:3178](I:/workspace/mobilegame/local-server/ljjc/systems.py:3178) |
| P2 有实现即接近完成 | 多数是沙盒占位接口，与真实客户端路由或结构不一致；必须逐功能完成入口、请求、结算、持久化闭环 | [systems.py:2631](I:/workspace/mobilegame/local-server/ljjc/systems.py:2631)、[realtime_pvp.py:344](I:/workspace/mobilegame/local-server/ljjc/realtime_pvp.py:344) |

### 待定规则与默认处理建议

这些是实施前的规则选择，不阻塞现状核查与无争议修复；本轮未替用户确认。

- 首充建议每档一次，等量赠送非付费迷子，与常规赠送不叠加；旧订单是否消耗首次资格，需要一并确定。
- 日常按北京 06:00，周常按周一 06:00；十日/每日签到建议跟客户端按北京 00:00。新手连续登录分别核对客户端语义，不混用日键。
- 月卡建议漏登奖励作废，天数照常流逝；购买当天发一份并计入 30 天，同日续费不重发当天奖励。
- 既有本地数值与旧存档角色先保留，包括旧免费十连产生的角色。任何回收或重算另立迁移方案；原计划不能自动授权扣回玩家资产。
- 角色礼物来源、随机宝箱内容等缺少依据的规则保留待研究状态，不通过虚构奖励完成接口。

## Implementation Plan

- [ ] 1. 固定真实运行基线：记录完整启动参数、源码摘要、实际存档路径和 v4 工件；快照业务源码及仓库外 Lua 工具，建立可回滚版本，避免默认启动脚本切换到另一份存档。

- [ ] 2. 在隔离目录取得新的测试基线：保存全量结果、失败明细和环境版本；对真实存档使用 SQLite 在线备份，并验证副本可恢复。后续时间推进与故障注入只用隔离账号和副本。

- [ ] 3. 并行做补丁无更新协议验证：核对完整启动链、纯 JSON 响应、空 history 与 v4 版本条件，记录结果后再选服务端修改方案；保留当前补丁和缓存作为对照证据。

- [ ] 4. 单负责人统一时间策略：复用 core/clock.py，补 reset.py 的业务边界、月边界与配置日期解析；把登录时区、模块构造和启动配置连成一条链，区分午夜签到与六点日常。

- [ ] 5. 完成时间状态迁移与签到排期：逐项迁移重置标记、免费抽卡时间、日周门票、限购和活动锚点；兼容排期覆盖文件，记录迁移版本并保证重复启动不重复发奖，再解除跨文件写入锁定。

- [ ] 6. 修复 P0 回包和角色完整性：基础资源接口复用 14 类资源补齐逻辑并返回列表；角色在最终品质确定后初始化天赋，抽卡及所有发奖入口统一返回完整记录，重登保持天赋不变。

- [ ] 7. 建立可重入的结算入口与在线调度：在共享业务锁和原子提交范围内保存余额与结算标记；进程只创建一个调度器，在线 tick、请求补结算及停机恢复复用该入口，提交后再通知客户端。

- [ ] 8. 接通邮件、成就、勋章与重置推送：复用现有 notifier，按 descriptor 编码 ScMails，扩展参数传递并扇出至两个监听器；删除无效消息，同时保留特殊任务，验证推送触发的后续请求。

- [ ] 9. 收尾资源兜底与小路由：提前封住未知 task 路由赠币，单独处理 extra-activepoint；规范重复斜杠，审查 RolePresent 空结构和已知路径遮蔽，确保未知请求不修改玩家状态。

- [ ] 10. 统一付费账目和交付约束：由同一负责人修改 economy.py 与 mall.py，消除 5/27 双写；交付阶段重验限购、开放期与幂等，覆盖多张待交付订单竞争，修复后再开放更多付费入口。

- [ ] 11. 实现真实月卡闭环并替换旧测试：接 107 开关、购买天数、按自然结算日过期、每日邮件与在线余额刷新；以真实客户端购买路径替换旧 recharge 测试，覆盖后再退役旧接口。

- [ ] 12. 实现首充和直购礼包契约：规则确认后持久化每档首次资格并生成 Midas 同形活动数据；直购同时开放类型 37 入口和商品活动，验收再次购买、续费、限购与重复交付。

- [ ] 13. 分组完成体验修复：角色负责人处理继承路由、素材消耗与引用清理；社交负责人处理留言字段、旧数据和单调 ID；集成负责人处理红点持久化与聚合，满体力回法单独验证客户端表现。

- [ ] 14. 后端契约稳定后统一制作 v5：纳入 IAP、登录 socialEntry、图鉴过滤及选定的体力修复；离线构建和切换 force-patch 清单分开执行，保留 v4 回滚包，并核对升级与重复启动。

- [ ] 15. 逐个开发 P2 最小闭环：先好友申请与聊天，再公会加入、查看、捐献，再竞技场和竞速；福利与养成先完成资格、扣费和结算后开放页签，旧占位路由显式退役，P3 继续暂缓。

- [ ] 16. 每批完成独立发布验收：运行相关测试和阶段全量回归，备份最新存档后按真实参数部署；绑定源码、配置、补丁版本与报告，完成模拟器操作、重连和重启验证后再标记该批完成。

### 批次、依赖与分工

| 批次 | 内容 | 前置条件 | 完成标志 |
|---|---|---|---|
| A 基线 | 任务 1–2 | 无 | 可恢复的状态副本、代码快照、新测试报告、真实启动清单 |
| B 时间 | 任务 4–5 | A；签到边界确定 | 同业务时间口径一致，旧档迁移幂等，日周边界通过 |
| C 日常稳定 | 任务 6–9 | B；基础资源先于 BaseResChange 上线 | 抽卡即开天赋、跨日刷新、在线新邮件、未知请求不发奖 |
| D 付费与体验 | 任务 10–13 | B/C 相关依赖；付费规则确定 | 账目守恒、月卡正确过期、首充/礼包闭环、体验修复验收 |
| E 客户端交付 | 任务 3、14 | 协议验证可从 A 并行；发布等待对应后端 | 老版升一次，同版不重复下载，v5 界面与奖励一致 |
| F 机器人玩法 | 任务 15 | 基础时间、资源、推送稳定 | 每个玩法各自完成客户端入口至结算闭环 |

当前最多 4 个执行席位，安排为 1 名集成负责人加最多 3 名并行负责人，不同时启动全部工作组。

| 工作组 | 文件归属 | 避免冲突的约束 |
|---|---|---|
| 集成 | game.py、server.py、entering.py、统一配置和发布清单 | 所有共享入口改动汇总到一人；不与业务组同时编辑同一文件 |
| 时间批次 | core/clock.py、规划中的 reset.py、各业务时间调用点、排期 | 跨模块改造期间单一写入负责人；其他人可读代码、做协议研究 |
| 付费 | economy.py、mall.py、付费专项测试 | 两文件必须同一负责人，避免底层联动与商城双写分离上线 |
| 角色 | actors.py、systems.py、grants.py 相关改动和角色测试 | 继承、天赋、任务兜底集中排队，不拆给多个同时写 systems.py 的人 |
| 社交/活动 | social.py；events.py 与排期由另一个连续工作批次负责 | 受席位限制分批进行；新模块的路由接线仍交集成负责人 |
| 客户端 | workspace/runtime 下的 login/auth/IAP Lua、构建脚本及候选包 | 一人统一出包；其他人只提供修改要求，最终清单由集成负责人切换 |

原计划的 S/M/L 可用于比较复杂度，不能直接当交付承诺。时间迁移、无更新协议、竞技场副本与聊天协议存在未决点，完成各自小验证后再细估；多 agent 主要缩短独立模块开发，不能缩短共享文件接线和模拟器串行验收。

## Verification Criteria

- 基线：新报告记录源码摘要、实际测试命令、通过/失败数量及环境；历史失败与本次新增失败分开；不以 464 或 68/84 作为当前完成凭证。
- 时间：北京时间 05:59:59→06:00:00、23:59:59→00:00:00、周日→周一及月底边界均有对应业务断言；Unix 时间本身不平移；午晚餐窗口按本地小时开放。
- 迁移：旧档副本连续迁移两次结果相同；停机跨多日、时钟回拨、同日重复请求不重复发奖；免费抽卡和门票的日周补充、上限及限购周期正确。
- 并发与推送：请求与调度同时结算、HTTP/TLS 双监听、进程重启均不双发；通知失败后重连仍能读取已提交状态；ScMails 按 descriptor 解码，成就与物品参数正确，特殊任务不丢失。
- 角色与资源：充值后基础资源是完整 14 类列表；抽卡、礼包、邮件、角色道具获得的角色立即打开天赋页均成功；最终品质与天赋数量匹配，重登不重随机。
- 支付：付费迷子不大于总迷子且不为负；先扣非付费；首次、再次、混合赠送及重复交付账目正确；两张 pending 订单不能突破同一限购，交付失败不留下半笔发货。
- 月卡：真实入口购买后当天一封；同日请求、续费和重启不重发；模拟连续 30 天和中途离线多日，剩余天数与奖励符合确定规则；无月卡无法领取。
- 体验：留言超过 50 条后 ID 仍唯一，删除与旧留言兼容；继承只消耗合法素材并清理引用；已读红点重登保持；签到第 8–10 天可领且重复领取无新增收益；未知路由不赠币。
- 补丁：旧版升级一次，同版至少 3 次冷启动不再下载；失败可重试；v4 可回退且现有存档保留；首充角标、下单显示、到账与再次购买一致。
- 每批发布：在模拟器上留下操作结果、请求/推送证据和必要截图；至少完成登录→主城→一场战斗→结算→重登烟测，并检查该批涉及的界面。未经验证的玩法保持未完成状态。

## Potential Risks and Mitigations

1. **误用默认启动器，切换存档或丢失 TLS/补丁参数。** Mitigation：以当前进程和管理面为准生成部署清单，重启后比对 stateDir、监听端口与补丁版本。当前默认启动器路径见 [start-service.ps1:5](I:/workspace/mobilegame/local-server/ljjc/start-service.ps1:5)。

2. **源码、进程、客户端补丁版本不一致。** Mitigation：每次发布绑定三者摘要；当前 mall.py 晚于进程启动的差异先通过基线记录明确，再在完成验证后受控重启，不靠文件修改时间宣称上线。

3. **备份未包含排期和补丁，或复制数据库漏 WAL。** Mitigation：复用 SQLite 在线备份，额外快照排期覆盖文件、Lua、工件和 force-patch 清单；备份实现在 [server/core/backup.py:77](I:/workspace/mobilegame/local-server/ljjc/server/core/backup.py:77)，仅自动覆盖指定数据库。

4. **跨日迁移、调度和 HTTP 请求相互覆盖状态。** Mitigation：共享锁、持久化结算键与余额在同一事务内更新，提交后通知；故障注入覆盖提交前后崩溃，不能只照搬每日备份线程。

5. **构建脚本直接改在线清单。** Mitigation：拆离线出包与最后发布动作。现交付脚本会写 force-patch 清单，见 [deliver_patch.py:31](I:/workspace/mobilegame/runtime/deliver_patch.py:31)，调查和试包阶段不直接运行该发布步骤。

6. **开放活动时暴露占位奖励和免费 VIP。** Mitigation：资格、消费、结算先通过后再开放页签；目前条件奖励固定返回资格、养成 VIP 不扣费，见 [events.py:979](I:/workspace/mobilegame/local-server/ljjc/events.py:979)、[systems.py:2142](I:/workspace/mobilegame/local-server/ljjc/systems.py:2142)。

## Alternative Approaches

1. **推荐：先统一时间，再分批补协议和结算。** 时间跨文件改造集中处理，完成后允许独立模块并行；补丁协议研究同步进行，但其不确定性不阻塞已经验证可发布的服务端修复。

2. **若急需修复当前客户端报错：先单独发布基础资源回包与完整角色序列化。** 这两项不依赖具体签到规则，可形成短批次；随后仍回到时间统一，在线调度不得先行。

3. **不采用：一次性全量并改、先清缓存再判断补丁、只加活动入口。** 这些方式分别放大共享文件冲突、丢失故障证据、暴露占位结算；不能减少真实客户端验收工作。

### 本轮核查边界

P0 六项和 P1 核心账目、月卡、继承、留言、红点均有源码核对；补丁结论交叉检查已有逆向记录。P2 主要验证路由与占位实现的差距，没有证明机器人玩法可玩。体力满的最终错误提示、角色礼物来源、全部图鉴过滤范围、聊天推送及所有 P2 实机表现仍需专项验证。
