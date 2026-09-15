# 知枢生产 Compose

本目录保存 Nginx、Prometheus、Grafana、OpenTelemetry Collector、告警和安全备份脚本。完整操作顺序与故障处理见 [`docs/operations-runbook.md`](../docs/operations-runbook.md)。

最短启动流程：

```powershell
Copy-Item .env.production.example .env.production
# 将所有 replace-with-* 占位符替换为本机临时随机值
docker compose -f docker-compose.production.yml --profile ops run --rm migrate
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
```

正式配置要求可信TLS终结。仅在本机loopback验收且没有TLS终结器时，用第二个Compose文件显式关闭Secure Cookie/重定向：

```powershell
docker compose -f docker-compose.production.yml -f docker-compose.production.local.yml up -d
```

本机业务入口：`http://127.0.0.1:18080`；Grafana：`http://127.0.0.1:13000`；Prometheus：`http://127.0.0.1:19090`；Jaeger：`http://127.0.0.1:16686`。这些端口默认只监听 loopback。

生产公网部署必须在此入口前配置可信 TLS 终结、真实域名和备份策略。不要直接提交 `.env.production`、dump 或 media。
