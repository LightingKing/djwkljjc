# 局域网部署与 TLS

`start-service.ps1` 保持回环 HTTP 启动，避免把账号和游戏流量明文暴露到
局域网。需要给手机或其它设备联调时，使用 `start-lan.ps1`：它要求显式提供
非回环绑定地址、证书和私钥，生成 `runtime/server-lan.toml`，并以
`server.py --tls-only` 启动。`--tls-only` 会完全跳过 HTTP listener；
`enable_tls=true` 本身不会自动关闭 HTTP，因此不能把普通 `server.py` TLS
启动误当成 TLS-only 部署。

```powershell
.\start-lan.ps1 `
  -BindHost 192.168.1.20 `
  -Cert C:\certs\ljjc.crt `
  -Key C:\certs\ljjc.key `
  -TlsPort 19102
```

脚本不会改写源 `server.toml`，也不自动生成或安装证书。证书信任需要在补丁
客户端的网络配置中单独验收；脚本的健康检查只为本机 readiness probe 临时跳过
证书验证。Windows PowerShell 5.1 生成的 TOML 使用无 BOM UTF-8 写入，服务端
也兼容 `utf-8-sig`，避免启动时解析失败。

服务端启动前会校验 `bind_host` 和 `public_base_url`：只允许回环、私有网段、
链路本地、通配地址或 `localhost`，拒绝公网地址和任意 DNS 主机名，也拒绝 URL
中的凭据、查询参数和额外路径。这样目录响应不会意外把客户端引向官方或其它
公网服务；`0.0.0.0` 只表示监听所有本机接口，客户端仍应使用实际的私有 LAN 地址。

已验证：使用临时自签名证书、`0.0.0.0:19443` 启动成功，日志记录
`TLSv1.3` 握手和 `/__local__/health` 200；源 `server.toml` 保持不变，备份目录
落在 `runtime/backups`。仍需在真实局域网、手机证书信任和 Windows 专用网络
防火墙规则上做 M1.11/M1.12 设备验收。
