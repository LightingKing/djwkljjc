#!/usr/bin/env python3
"""Build a conservative, evidence-backed ROADMAP acceptance matrix.

The matrix is intentionally explicit about client-side and source-evidence
blockers.  A passing service test cannot promote a client task to complete,
and a discovered APK type cannot promote a missing server contract to
recovered.  The script only checks local artifacts and never starts an APK or
contacts a retired service.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Item:
    code: str
    status: str
    evidence: tuple[str, ...]
    note: str


def _exists(root: Path, relative: str) -> bool:
    return (root / relative).exists()


def items(root: Path) -> tuple[Item, ...]:
    def artifact(*paths: str) -> tuple[str, ...]:
        return tuple(path for path in paths if _exists(root, path))

    return (
        Item("M0.1", "blocked", artifact("docs/设备报告.md"), "真实手机 ABI/转译器证据缺失；当前按路线 B。"),
        Item("M0.2", "pending_review", artifact(".gitignore", "README.md", "ROADMAP.md"), "首次 Git 提交按路线图留待用户审阅。"),
        Item("M0.3", "verified", artifact("docs/工具链.md", "tools/verify-toolchain.ps1", "tools/bootstrap-toolchain.ps1", "tests/test_toolchain_bootstrap.py"), "工具链检查和默认只读 bootstrap 探测已通过；显式 -Install 才会运行 pip/sdkmanager。"),
        Item("M0.4", "blocked", artifact("dev/cold-start-check.ps1", "docs/客户端UI验收-2026-10-28.md"), "AVD 可启动，但补丁包被 TPShell clean exit 阻断。"),
        Item("M0.5", "skipped", artifact("data/golden-traces"), "可选旧链路取证未形成完整全量回放。"),
        Item("M1.1", "verified", artifact("docs/客户端结构.md", "docs/APK静态分析-2026-10-28.md"), "组件和通信边界有静态/运行时证据。"),
        Item("M1.2", "verified_static", artifact("client-patch/build.py", "docs/客户端补丁验收.md"), "补丁构建、target24 和签名静态验收通过。"),
        Item("M1.3", "blocked", artifact("docs/客户端补丁验收.md"), "完整性链路阻断；未删除、关闭或伪造校验。"),
        Item("M1.4", "partial", artifact("client-patch/patches/ljjc-offline-sdkshim-2.0.4.json", "docs/客户端补丁验收.md"), "MSDK 已有 shim；其它组件客户端端到端未通过。"),
        Item("M1.5", "blocked", artifact("tools/audit_lua_decompilation.py", "docs/M2.3_Lua全量反编译.md"), "858 个静态保护载荷缺少解码密钥。"),
        Item("M1.6", "partial", artifact("server.toml", "server.py"), "本地动态配置已实现，APK 引导注入仍受 G1 阻塞。"),
        Item("M1.7", "partial", artifact("server.py", "tests/test_server.py"), "服务端 TLS 通过；客户端自建 CA/Unity 信任未验收。"),
        Item("M1.8", "blocked", artifact("client/"), "客户端账号 UI 未完成真实 APK 联调。"),
        Item("M1.9", "verified", artifact("accounts.py", "docs/账号体系.md"), "本地账号、密码、游客绑定和会话回归通过。"),
        Item("M1.10", "partial", artifact("economy.py", "client/"), "服务端沙盒订单/发货完成；客户端确认 UI 未验收。"),
        Item("M1.11", "partial", artifact("start-server.cmd", "start-lan.ps1", "server.toml", "docs/LAN部署与TLS.md"), "回环启动和 TLS-only LAN 启动/本机健康检查通过；真实手机、专用网络防火墙和证书信任未验收。"),
        Item("M1.12", "blocked", artifact("docs/客户端UI验收-2026-10-28.md"), "真实客户端 G1 阻塞，无法完成手机/模拟器全链路。"),
        Item("M2.1", "verified", artifact("data/apk-inventory.json", "docs/APK清单.md"), "APK 清单已生成；完整 AssetBundle 依赖仍按证据边界描述。"),
        Item("M2.2", "partial", artifact("data/config", "data/config-source-audit.json", "tools/audit_config_sources.py", "docs/配置抽取.md"), "29 张运行时表有抽取和完整性标记；APK 清单另有 187 个 xlsx 表名，其中 168 个只有资源路径，受保护载荷未宣称已解码。"),
        Item("M2.3", "blocked", artifact("analysis/lua/decompilation-audit.json", "docs/M2.3_Lua全量反编译.md"), "保护载荷闭环未完成。"),
        Item("M2.4", "verified_static", artifact("analysis/il2cpp/structure-audit.json", "docs/M2.4_IL2CPP结构审计.md", "analysis/il2cpp/dump-v6.7.46-x86/dump.cs", "analysis/il2cpp/ghidra-project-x86"), "Il2CppDumper v6.7.46 输出和 Ghidra x86 导入已验证；自动分析 300 秒超时，未宣称完整符号分析。"),
        Item("M2.5", "partial", artifact("contracts/api.yaml", "analysis/protocol-surface-audit.json", "docs/M2.5协议面审计.md"), "84 条 HTTP 契约和类型面审计完成，不能等同于早期 342/66 估算。"),
        Item("M2.6", "partial", artifact("tools/audit_descriptors.py", "docs/Protobuf描述符审计.md"), "23/23 契约消息可解析；其中 21 个 C# 消息类与 descriptor 字段号集合匹配；缺失描述仍只审计、不凭空补字段。"),
        Item("M2.7", "partial", artifact("data/server-only-value-audit.json", "data/server-only/historical-evidence.yaml", "docs/数值来源.md"), "4 个规则/证据文件的叶子值都有来源标记；公开历史资料补充为 C 级证据，完整官方 A/B 级数值仍缺失。"),
        Item("M2.8", "partial", artifact("data/content-audit.json", "data/config-source-audit.json", "tools/audit_config_sources.py", "docs/内容清单.md"), "资源证据已核对；当前样本的 6 个章节行和 87 个普通/挑战入口已覆盖，另有 168 个 APK xlsx 表名没有可验证解码结果，完整后续资源仍有缺口。"),
        Item("M3.1", "verified", artifact("server/core/routes.py", "contracts/api.yaml"), "统一路由和 501 计数已验证。"),
        Item("M3.2", "verified", artifact("storage/compat.py", "tools/backup_restore.py", "docs/统一存储与账本.md"), "统一 SQLite/WAL、迁移和备份恢复已验证。"),
        Item("M3.3", "verified", artifact("economy.py", "tests/test_storage_ledger.py"), "资源账本原子性和幂等回归通过。"),
        Item("M3.4", "verified", artifact("server/core/clock.py", "tests/test_long_term_stability.py"), "日切、周桶和恢复回归通过。"),
        Item("M3.5", "partial", artifact("data/local-defaults.json", "data/config/PlayerLevelInitConfig.json", "data/config/Tutorial.json", "game.py", "systems.py", "docs/M3.5初始化与教程.md"), "初始化和教程可运行；step 进度跨重启持久化并按观测顺序门控；完整官方 PlayerInitConfig/Tutorial 表未恢复。"),
        Item("M3.6", "partial", artifact("dungeon.py", "tests/test_dungeon_expanded.py", "docs/内容清单.md"), "配置驱动副本子集完成；ChapterInfo 样本的 87 个普通/挑战入口逐个可进入，DungeonMonster 1–4 与 StoryDungeon 1001–1004 的阵容证据可查询，完整官方副本集未恢复。"),
        Item("M3.7", "partial", artifact("server/core/push_service.py", "tests/test_push_service.py"), "本地推送会话和事件完成；真实客户端刷新未验收。"),
        Item("M3.8", "verified", artifact("tools/contract_harness.py", "tools/audit_local_evidence.py", "analysis/local-evidence-audit.json", "dev/contract-smoke-report.json"), "84/84 安全探针通过，并已将本地实现覆盖率与未注册路由安全性分开审计。"),
        Item("M4", "partial", artifact("docs/M4角色装备道具.md", "tests/test_systems.py", "dev/local_playability_smoke.py", "docs/本地玩法烟测.md"), "安全事务子集和端到端本地烟测完成，完整官方数值和客户端入口未验收。"),
        Item("M5", "partial", artifact("docs/M5任务成就邮件沙盒.md", "docs/M5网页与小游戏沙盒.md", "tests/test_events.py", "dev/local_playability_smoke.py", "docs/本地玩法烟测.md"), "本地活动/任务/邮件/小游戏子集和端到端本地烟测完成。"),
        Item("M6", "partial", artifact("docs/M6机器人社交PVP.md", "docs/M6.6实时PVP.md", "tests/test_cross_process_state.py", "dev/local_playability_smoke.py", "docs/本地玩法烟测.md"), "机器人和同机跨进程一致性、端到端本地烟测完成，官方实时服务/跨机器真人协作未恢复。"),
        Item("M7", "verified", artifact("start-server.cmd", "docs/管理面板与备份.md", "server/core/backup.py"), "启动、管理、备份和日志已验证。"),
        Item("M8.1", "partial", artifact("docs/功能验收清单.md", "tools/build_acceptance_checklist.py"), "服务端状态分层生成；客户端项目仍因 G1 未勾选。"),
        Item("M8.2", "partial", artifact("tests", "dev/ui-smoke-report-api30.json", "dev/local_playability_smoke.py", "docs/客户端UI验收-2026-10-28.md"), "223 项 pytest 通过；本地玩法烟测通过，客户端 UI/冷启动仍阻塞。"),
        Item("M8.3", "partial", artifact("tests/test_long_term_stability.py", "dev/m8_acceptance.py", "docs/M8长期稳定与交付.md"), "28 天服务端自动化回归、时钟回拨边界、备份/日志和动态证据脚本通过；真实客户端长期运行未验收。"),
        Item("M8.4", "verified", artifact("tools/package-offline.ps1", "dev/m8_acceptance.py", "delivery/ljjc-offline-server.zip"), "交付包临时解压健康检查和逐文件清单哈希通过，排除 APK/密钥/存档。"),
    )


def build_report(root: Path) -> dict[str, object]:
    rows = []
    for item in items(root):
        rows.append({"code": item.code, "status": item.status, "evidence": list(item.evidence), "note": item.note})
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    return {"schemaVersion": 1, "root": str(root), "counts": counts, "items": rows}


def render(report: dict[str, object]) -> str:
    lines = [
        "# ROADMAP 逐项验收矩阵",
        "",
        "此表由 `tools/audit_roadmap.py` 生成。`verified` 只表示该范围的本地证据已通过；`partial` 表示本地兼容子集或部分数据；`blocked` 表示当前有明确外部/客户端阻塞；`pending_review` 表示按路线图需要用户审阅后才能完成。服务端单测不会自动把客户端状态标为完成。",
        "",
        "| 项目 | 状态 | 证据 | 说明 |",
        "|---|---|---|---|",
    ]
    for item in report["items"]:  # type: ignore[index]
        evidence = ", ".join(f"`{path}`" for path in item["evidence"])
        lines.append(f"| `{item['code']}` | `{item['status']}` | {evidence or '（缺少）'} | {item['note']} |")
    lines.extend(["", f"状态计数：`{json.dumps(report['counts'], ensure_ascii=False, sort_keys=True)}`", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", type=Path, default=Path("analysis/roadmap-audit.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/ROADMAP验收矩阵.md"))
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_report(root)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render(report), encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_report", "items", "render"]
