# 第十五阶段运维手册

本手册针对仓库根目录的 `docker-compose.production.yml`。命令默认在项目根目录运行。真实生产应由受控 TLS 入口转发至 Nginx，示例端口只绑定 `127.0.0.1`。

## 1. 首次部署

```powershell
Copy-Item .env.production.example .env.production
py -3.10 -c "import secrets; print(secrets.token_urlsafe(64))"
py -3.10 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

分别把输出写入 `DJANGO_SECRET_KEY/AUDIT_IP_HASH_KEY` 和 `MODEL_CONFIG_ENCRYPTION_KEY`，再设置 PostgreSQL、Grafana 密码、域名、`ALLOWED_HOSTS` 与 `CSRF_TRUSTED_ORIGINS`。正式配置默认Secure Cookie和HTTPS重定向，必须由可信入口终结TLS，并把`X-Forwarded-Proto=https`传入Nginx。不要把输出粘到Issue、日志或Git。

```powershell
docker compose -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml --profile ops run --rm migrate
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml ps
```

只有在本机loopback验收且没有TLS终结器时，才叠加本地覆盖；不能把该覆盖部署到公网：

```powershell
docker compose -f docker-compose.production.yml -f docker-compose.production.local.yml up -d
```

## 2. 配置检查

```powershell
docker compose -f docker-compose.production.yml run --rm --no-deps backend python manage.py check --deploy
docker compose -f docker-compose.production.yml run --rm --no-deps nginx nginx -t
```

必须满足：`APP_ENV=production`、`DEBUG=false`、独立 Django/Fernet/Audit 密钥、PostgreSQL、共享 Redis Cache、`CELERY_TASK_ALWAYS_EAGER=false`。模型环境变量允许为空，此时业务使用安全本地回退。

## 3. 启动、停止和状态

```powershell
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml stop
docker compose -f docker-compose.production.yml restart backend worker
docker compose -f docker-compose.production.yml ps
docker stats --no-stream
```

不要在保留数据需求未确认时执行 `down -v`；`-v` 会删除该 Compose 的 PostgreSQL、Redis、media、Prometheus 和 Grafana卷。

## 4. 健康检查

```powershell
curl.exe -i http://127.0.0.1:18080/health/live/
curl.exe -i http://127.0.0.1:18080/health/ready/
```

`live=200`只说明应用进程存活；`ready=503`说明数据库、Redis或生产配置至少一项不可用。详细依赖接口需要管理员登录和 `X-Workspace-ID`，不要公开给匿名网络。

## 5. 查看和关联日志

```powershell
docker compose -f docker-compose.production.yml logs --since 10m backend
docker compose -f docker-compose.production.yml logs --since 10m worker
docker compose -f docker-compose.production.yml logs --since 10m nginx
docker compose -f docker-compose.production.yml logs --since 10m backend worker | Select-String 'request_id值'
docker compose -f docker-compose.production.yml logs --since 10m backend worker | Select-String 'trace_id值'
docker compose -f docker-compose.production.yml logs --since 10m worker | Select-String 'task_id值'
```

浏览器响应头 `X-Request-ID` 是一次 HTTP 请求的关联键；Jaeger中的32位 `trace_id` 可跨 HTTP、Celery和模型 Span。日志不应出现完整 Cookie、Authorization、API Key、邀请或公开Token。

## 6. Prometheus、Grafana和Jaeger

- Prometheus：<http://127.0.0.1:19090/targets>
- Grafana：<http://127.0.0.1:13000>
- Jaeger：<http://127.0.0.1:16686>

```powershell
curl.exe http://127.0.0.1:19090/-/healthy
curl.exe "http://127.0.0.1:19090/api/v1/query?query=up"
curl.exe http://127.0.0.1:16686/
```

Grafana应自动出现“知枢｜系统总览”“知枢｜RAG 与模型”“知枢｜Celery 与文档任务”。业务入口 `/internal/metrics/` 应返回404。

## 7. 查看队列积压和重启 Worker

```powershell
docker compose -f docker-compose.production.yml exec redis redis-cli LLEN celery
docker compose -f docker-compose.production.yml exec worker celery -A config inspect ping
docker compose -f docker-compose.production.yml exec worker celery -A config inspect active
docker compose -f docker-compose.production.yml restart worker
docker compose -f docker-compose.production.yml logs --since 5m worker
```

不要直接清空 Redis 队列。先判断是模型慢、Worker离线、数据库阻塞还是任务本身失败；数据库 `DocumentProcessingTask` 才是审计事实。

## 8. Migration

```powershell
docker compose -f docker-compose.production.yml stop backend worker beat
docker compose -f docker-compose.production.yml --profile ops run --rm migrate
docker compose -f docker-compose.production.yml up -d backend worker beat
```

只运行一个 migrate 实例。部署前先做数据库备份；存在不可逆 Migration 时必须另写回滚方案。

## 9. PostgreSQL备份

```powershell
docker compose -f docker-compose.production.yml exec postgres sh /opt/zhishu/scripts/backup_postgres.sh
Get-ChildItem .\backups\stage15-*.dump | Sort-Object LastWriteTime -Descending
```

脚本生成 custom-format dump 和 SHA256。`backups` 已忽略 Git。数据库备份不包含 media 文件。

## 10. 临时恢复演练

先从上一命令记录确切文件名，只允许恢复到包含 `stage15` 和 `restore` 的独立库：

```powershell
docker compose -f docker-compose.production.yml exec postgres sh /opt/zhishu/scripts/restore_stage15.sh stage15-zhishu-YYYYMMDDTHHMMSSZ.dump zhishu_stage15_restore
docker compose -f docker-compose.production.yml exec postgres psql -U $env:POSTGRES_USER -d zhishu_stage15_restore -c "select count(*) from api_knowledgebase;"
docker compose -f docker-compose.production.yml exec postgres dropdb -U $env:POSTGRES_USER --if-exists zhishu_stage15_restore
```

恢复脚本会校验名称和SHA256，并拒绝普通目标库名。演练记录备份字节数、备份/恢复耗时及User、Organization、Workspace、KnowledgeBase、Document、Paragraph、Conversation、Message、ProcessingTask和AuditEvent计数。

## 11. media备份边界

数据库中的文件路径必须与相同时点的media卷配套。临时导出示例：

```powershell
docker run --rm -v zhishu-production_media_data:/media:ro -v ${PWD}/backups:/backups alpine:3.20 sh -c "tar -czf /backups/stage15-media.tar.gz -C /media . && sha256sum /backups/stage15-media.tar.gz > /backups/stage15-media.tar.gz.sha256"
```

恢复media前必须停写并核对目标卷，不能覆盖未备份的真实文件。本阶段没有对象存储版本化能力。

## 12. 模型超时与429

1. 从前端安全提示取得 Request ID。
2. 在后端日志和Jaeger按 Request/Trace ID定位 `llm.chat` 或 `embedding.batch`。
3. 查看 `model_timeouts_total`、`model_rate_limits_total` 和 P95。
4. 核对模型配置的超时、Base URL、模型名与额度，不输出API Key。
5. Chat失败不应保存空AI消息；Embedding重处理失败应保留旧Paragraph。

## 13. Redis异常

```powershell
docker compose -f docker-compose.production.yml ps redis
docker compose -f docker-compose.production.yml logs --since 10m redis
docker compose -f docker-compose.production.yml exec redis redis-cli ping
docker compose -f docker-compose.production.yml restart redis
```

Redis异常时 ready 应为503，上传投递应返回安全错误，已有数据库记录不得伪装成已执行。恢复后核对队列与任务表，再决定显式重试。

## 14. PostgreSQL异常

```powershell
docker compose -f docker-compose.production.yml ps postgres
docker compose -f docker-compose.production.yml logs --since 10m postgres
docker compose -f docker-compose.production.yml exec postgres pg_isready -U $env:POSTGRES_USER -d $env:POSTGRES_DB
```

不要通过删除Volume“修复”数据库。先保留日志和备份，确认磁盘、连接数与迁移状态；live可继续200，但ready必须503。

## 15. 回滚版本

```powershell
git status --short
git log --oneline -5
# 切换到已确认的版本或镜像标签后：
docker compose -f docker-compose.production.yml build backend nginx worker beat
docker compose -f docker-compose.production.yml up -d backend nginx worker beat
```

若新版已执行不可逆数据库迁移，仅回滚镜像可能不安全；必须按该Migration的专用方案处理。

## 16. 告警 Runbook

### BackendDown

检查容器、live、最近启动日志与Gunicorn退出原因；确认不是Prometheus自身网络问题。

### HighHttp5xxRate

按route/status_class定位，再按Request ID看异常类型；不要通过提高阈值掩盖持续错误。

### HighHttpP95Latency

分离普通API、SSE、检索与模型延迟，检查数据库慢查询、线程占用和外部模型。

### NoCeleryWorkers

检查Worker容器、metrics 9101、Broker连接和进程退出；恢复后验证新任务，不直接清队列。

### CeleryQueueBacklog / CeleryOldestTaskTooOld

核对LLEN、active任务、Worker并发、任务阶段和模型耗时；扩并发前检查数据库连接和模型限流。

### DocumentTaskFailureRateHigh / StaleProcessingTasks

按task_id查看安全错误码与最后更新时间；收敛器不会删除旧Paragraph，确认后显式重试。

### ModelTimeoutRateHigh / ModelRateLimited

检查模型服务SLA、额度、超时和请求频率；禁止在日志或工单粘贴Key。

### DatabaseUnavailable / RedisUnavailable

分别按第13、14节处理。告警有持续时间，瞬时抖动不应立即升级为事故。

## 17. stage15临时数据清理

优先让临时验收使用独立Compose数据库，验收后先保存报告，再执行：

```powershell
docker compose -f docker-compose.production.yml down
# 仅在已核对这是stage15独立临时栈且数据无需保留时：
docker compose -f docker-compose.production.yml down -v
```

不要在含真实用户数据的栈执行 `down -v`。应用内临时数据只能按明确 `stage15-` 前缀和租户逐项核对后删除。

## 18. 常见错误

- `spawn EPERM`：宿主Windows沙箱禁止esbuild子进程；使用Linux前端构建容器或CI，不能改`node_modules`规避。
- `ready=503 redis`：Broker/共享缓存不可达；检查Redis而不是重启全部服务。
- `CSRF verification failed`：核对HTTPS终结、`X-Forwarded-Proto`和`CSRF_TRUSTED_ORIGINS`。
- SPA刷新404：检查Nginx `try_files ... /index.html`。
- SSE最后一次性出现：确认匹配聊天location且`proxy_buffering off`。
- Grafana无数据：先看Prometheus `/targets`，再看指标名与时间窗口。

## 19. 紧急处理边界

允许：只读状态/日志/指标/Trace、重启无状态Web/Worker、对已验证备份做独立恢复。需要审批：删除Volume、清空Redis、终止数据库会话、回滚Migration、恢复覆盖、轮换主密钥、关闭权限/SSRF/CSRF或把内部端口开放公网。
