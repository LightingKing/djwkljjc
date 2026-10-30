#!/usr/bin/env python3
"""Generate the M8.1 interface acceptance checklist from contracts/api.yaml."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def _status(value: Any, *, live: str | None = None) -> str:
    if live == "implemented":
        return "服务端已实现，客户端未验证"
    if live == "unimplemented":
        return "服务端未实现（明确 501）"
    mapping = {
        "implemented": "服务端已实现，待客户端端到端",
        "fixture": "固定夹具，待客户端端到端",
        "unimplemented": "未实现（明确 501）",
        "observed_unverified": "已取证，待契约/客户端验证",
    }
    return mapping.get(str(value), "待确认")


def build(source: Path, coverage: dict[str, Any] | None = None) -> str:
    document = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    interfaces = document.get("interfaces", [])
    rows: list[str] = []
    live_status = {
        str(item.get("interface")): str(item.get("status"))
        for item in (coverage or {}).get("interfaces", [])
        if isinstance(item, dict) and item.get("interface")
    }
    for item in interfaces:
        if not isinstance(item, dict):
            continue
        interface = str(item.get("interface") or "")
        route = str(item.get("route") or "")
        status = _status(item.get("implementationStatus"), live=live_status.get(interface))
        client = "未验证（G1 阻塞）" if live_status else "待客户端验证"
        rows.append(f"| [ ] | `{interface}` | `{route}` | {status} | {client} | 请求成功、响应字段可解码、重启后状态保留 |")
    lines = [
        "# 功能验收清单（M8.1）",
        "",
        "本清单由 `tools/build_acceptance_checklist.py` 根据 `contracts/api.yaml` 生成。若传入 `--coverage`，服务端状态来自运行时 `RouteRegistry.coverage_report()`；勾选表示已经在本地离线服务端和客户端上实际操作过，单元测试通过不等于客户端验收通过。",
        "",
        "## 执行顺序",
        "",
        "1. 启动 `start-server.cmd`，确认 `/__local__/health` 返回 `status=ok`。",
        "2. 创建沙盒账号，完成登录、主城、教程 2011、故事 2012–2013 和已实现的首章普通关卡。",
        "3. 依次打开活动、签到、商城、沙盒支付、邮件、角色、装备、社交和 PVP 页面，记录请求路径、状态码、响应解码和截图。",
        "4. 服务端重启后复查资源、关卡、领取记录和订单状态；官方账号、真实支付和远程服务保持未连接。",
        "",
        "## 自动化基线",
        "",
        "```powershell",
        "python -m unittest discover -s .\\tests -q",
        "python -m compileall -q .",
        "python tools/contract_harness.py --write-sample-replay dev/contract-smoke.jsonl --report dev/contract-smoke-report.json",
        "```",
        "",
        "## 接口逐项记录",
        "",
        "| 完成 | 接口 | 路径 | 服务端状态 | 客户端状态 | 验收记录 |",
        "|---|---|---|---|---|",
        *rows,
        "",
        "## 已知边界",
        "",
        "当前实现是本地兼容子集。未完成接口保留 501，并在 `docs/内容清单.md` 和路线图中标记；不要把取证状态误写成已恢复官方服务端。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=Path("contracts/api.yaml"))
    parser.add_argument("-o", "--output", type=Path, default=Path("docs/功能验收清单.md"))
    parser.add_argument("--coverage", type=Path, help="optional RouteRegistry coverage JSON")
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"contract file does not exist: {args.source}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    coverage = None
    if args.coverage:
        coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    args.output.write_text(build(args.source.resolve(), coverage), encoding="utf-8")
    print(f"wrote acceptance checklist to {args.output}")


if __name__ == "__main__":
    main()
