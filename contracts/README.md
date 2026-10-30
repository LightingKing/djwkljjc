# 接口契约表

`api.yaml` 由 `tools/build_contracts.py` 从 `analysis/ljjc-local/protocols/interface-map.json` 生成，当前收录 84 个已从描述符确认的 HTTP 接口。它是 M2.5 的第一版底稿，保留请求字段、响应嵌套字段、原始 route 和来源。

当前每条记录状态为 `observed_unverified`，因为还没有为所有接口建立本地响应契约测试。已实现的接口会在后续回归测试覆盖后改为 `implemented`；捕获 fixture 和明确未实现的接口也会分别标记，避免把“看见接口”误报成“服务端已经恢复”。

## M3.8 契约 smoke 和流量回放

`tools/contract_harness.py` 会从这张表生成一条安全、确定性的 JSON 请求样例，逐条通过 `RouteRegistry` 分发。未注册的已知路由必须返回 `501 route_not_implemented`，而完全未知的路径必须返回 `404 route_not_found`；响应中不能带 traceback 或异常细节。

```powershell
python tools/contract_harness.py `
  --contracts contracts/api.yaml `
  --write-sample-replay dev/contract-smoke.jsonl `
  --report dev/contract-smoke-report.json
```

已经抓取的本地 JSONL 流量可以按同一格式回放：每行包含 `method`、`path`、`request`，可选 `interface` 和 `expectedStatus`。回放只在本地 registry 中执行，不连接官方服务，也不会修改 APK。

```powershell
python tools/contract_harness.py `
  --contracts contracts/api.yaml `
  --replay data/golden-traces/example.jsonl
```

样例数据由 `sample_request()` 生成，不包含账号密码、令牌或设备标识。契约 harness 的自动化测试位于 `tests/test_contract_harness.py`。
