# 第十五阶段：生产部署、全链路可观测性与故障恢复

> 状态：设计已完成，实施与真实验收记录将在每个垂直切片结束后更新。本文中的 SLO 均为初始目标，不是已经实测达成的承诺。

## 1. 现状审计

### 1.1 开发前基线（2026-09-12）

- 工作区包含第十四阶段尚未提交的用户改动；本阶段保留这些改动，不执行覆盖式 Git 操作。
- `py -3.10 manage.py test`：139 项通过，耗时 114.870 秒。
- `showmigrations`：`api` 0001～0013 均已应用；`makemigrations --check --dry-run` 无漂移；`manage.py check` 为 0 issues。
- `npm run type-check` 通过。宿主 Windows 沙箱执行标准 `npm run build` 时，Vite 加载配置阶段被环境拒绝创建 esbuild 子进程，错误为 `spawn EPERM`；未修改 `node_modules`，也未把 WASM 替代加入依赖。
- Docker CLI 29.1.3、Compose 5.0.0 可用；`docker-compose.stage08.yml config` 通过。当前 Codex 进程连接 Docker Desktop Linux Engine 时返回 Access denied，尚未完成真实容器启动。
- Stage 08 Compose 已有 PostgreSQL、Redis、Gunicorn、Celery Worker，但仍是学习拓扑：`DEBUG=true`、源码 bind mount、后端端口直出、无 Nginx/Beat/监控/Trace、迁移与 Web 启动绑在同一命令中。
- 自动化测试默认 `CELERY_TASK_ALWAYS_EAGER=true`；生产必须显式 false。任务协调实现实际位于 `backend/api/services/document_tasks.py`，任务书中列出的 `task_coordinator.py` 不存在。
- 问答与应用聊天使用同步 `StreamingHttpResponse` 和同步 OpenAI SDK。一个 SSE 连接会长期占用一个同步执行单元，当前代码并非原生 async Django/ASGI 链路。
- 现有 `DocumentProcessingTask.updated_at` 会随状态/进度更新，可作为任务最后心跳时间；尚无 Beat 收敛器和 Worker 在线心跳。
- 现有日志只是文本 `StreamHandler` 加敏感 URL 过滤；`AuditEvent` 和 `ApplicationAccessLog` 有业务 request_id，但没有全局请求中间件、统一响应 Header 或 trace_id。
- 没有 Prometheus 指标、OpenTelemetry、Grafana、告警规则或生产备份脚本。
- CI 已真实执行 Linux `npm ci`、类型检查与生产构建，但尚未构建/启动生产容器。
- 当前前端已采用路由懒加载和稳定 vendor 分包；入口业务脚本已缩小，但完整 Element Plus 主题仍会产生较大的非阻断包体警告。
- 当前数据库备份和上传文件备份没有形成可复制执行的运行手册。

### 1.2 已有安全资产与风险

已有：Session + CSRF、HttpOnly Cookie、工作空间 RBAC、审计事件、模型 Key Fernet 加密、应用凭证/邀请 Token 摘要存储、SDK 异常安全映射。

风险：反向代理若记录完整 URI，邀请/公开 Token 仍可能进入 access log；日志上下文不统一；健康和 metrics 路由若从 Nginx直出会泄漏内部状态；Redis/PostgreSQL若映射宿主公网端口会扩大攻击面；遥测属性若收集 Prompt、正文或用户标识会产生二次泄漏。

## 2. 范围与非范围

本阶段实现单机生产 Compose、Nginx、Gunicorn、PostgreSQL、Redis、独立 Worker 和单实例 Beat、JSON 日志、Request/Trace 传播、OpenTelemetry、Prometheus、Grafana、告警、健康检查、僵尸任务收敛、备份恢复工具、生产冒烟和压测口径。

本阶段不引入 Kubernetes、Kafka、服务网格、多地域、自动扩缩容、完整 ELK/Loki、商业 APM、pgvector、对象存储、SSO/MFA、新 Agent 工具或 MCP。向量仍保存在 JSON，文件仍使用共享本地 Volume。

## 3. 开发与生产差异

| 项目 | 开发/测试 | 生产 Compose |
| --- | --- | --- |
| 前端 | Vite dev server | 多阶段构建的静态 `dist`，由 Nginx 托管 |
| Django | `runserver`/SQLite 可选 | Gunicorn gthread + PostgreSQL |
| 异步任务 | 测试可 eager、内存锁 | Celery Worker、Redis Broker/锁，eager=false |
| 调度 | 无 | 单实例 Celery Beat |
| 密钥 | 本地 `.env` | `.env.production`/部署平台 Secret，不入镜像和 Git |
| 日志 | 可读文本 | stdout JSON |
| 遥测 | 默认可关闭 | OTLP Collector + Jaeger，失败时业务降级 |
| 指标 | 无 | Prometheus + Grafana；内部端口不对公网 |

## 4. 生产拓扑与容器职责

```text
Browser -> nginx:8080
             |-- /assets, / -> Vue dist
             |-- /api, /health -> backend:8000
             `-- /media -> shared media volume (read-only)

backend / worker / beat -> postgres:5432
backend / worker / beat -> redis:6379
backend / worker -> otel-collector:4317 -> jaeger:4317
prometheus -> backend:8000/internal/metrics
prometheus -> worker:9101/metrics
grafana -> prometheus
```

- `nginx`：唯一业务入口，静态资源、SPA fallback、普通 API 和 SSE 代理；媒体先由Django验证用户与工作空间，再通过内部 `X-Accel-Redirect` 只读传输。
- `backend`：单个 Gunicorn master + 1 个 gthread worker + 8 threads。第一版单实例避免 `prometheus_client` 多进程指标合并问题；可通过实例级横向扩展演进。
- `worker`：Celery threads pool，默认并发 4；文档任务是 I/O 与模型调用混合负载，第一版优先保证指标与 Trace 共享进程、可审计和资源可控。
- `beat`：唯一周期调度源，调度心跳和僵尸任务扫描。不得同时启动第二个 Beat。
- `postgres`：业务审计事实来源和持久数据；不映射宿主端口。
- `redis`：Broker、结果后端、分布式锁、共享限流和短期心跳；AOF 开启，仍不替代数据库事实。
- `otel-collector`：接收 OTLP、批处理并转发 Trace；不可用时 SDK 使用短超时并丢弃遥测，不阻塞业务。
- `jaeger`：唯一 Trace 后端。
- `prometheus/grafana`：仅绑定 `127.0.0.1`；Dashboard 与规则入库。

迁移由 `docker compose run --rm migrate` 的一次性服务显式执行，Backend 和 Worker 都不并发执行 migration。生产容器不挂载源码，不包含 `.git`、SQLite、本地上传和 `.env`。

## 5. 网络与端口边界

- 业务入口默认 `127.0.0.1:18080`，示例明确要求由上层 TLS 终结或显式改成受控监听；不会自动开放公网。
- Grafana、Prometheus、Jaeger 分别只绑定 `127.0.0.1:13000/19090/16686`。
- PostgreSQL、Redis、Backend、Worker metrics、Collector 无宿主端口映射。
- `internal/metrics` 只在容器网络中采集，Nginx 对外精确返回 404。

## 6. Nginx 与 SSE

- `/assets/` 使用内容哈希文件名，`Cache-Control: public,max-age=31536000,immutable`；`index.html` no-cache；`try_files $uri $uri/ /index.html` 支持 SPA 刷新。
- `/api/` 普通请求传递 `Host`、`X-Forwarded-*`、`X-Request-ID`，设置合理连接/读写超时。
- 聊天路径单独 `proxy_buffering off`、`proxy_cache off`、`gzip off`、较长 `proxy_read_timeout`，并传递 `X-Accel-Buffering: no`。
- `client_max_body_size` 与 Django 10 MiB 上限协调为 12 MiB。
- 不将完整 URI 写入 access log；生产 access log 使用 `$uri`，过滤 query。邀请/公开 Token 所在 path 进一步关闭 access log，由 Django 安全审计替代。
- `/media/`不直接映射文件目录：先代理到 `ProtectedMediaView` 鉴权，成功后只允许Nginx内部 `/_protected_media/` 读取，防止猜路径绕过租户权限。
- 加安全响应头、关闭目录浏览、隐藏 Nginx 版本。HTTPS 配置给出部署模板，但示例 Compose 不申请证书。

当前 SSE 同步生成，连接占用 gthread。Gunicorn `timeout` 必须大于 Nginx SSE read timeout，第一版分别为 180 秒和 150 秒；优雅停机 30 秒。发布时超过窗口的流会被终止，用户可从历史会话重试；成功完成前不会保存空 AI 消息。

## 7. 健康检查契约

- `GET /health/live/`：只证明 Django 进程和路由可响应；永远不探测外部依赖。
- `GET /health/ready/`：短超时检查关键表/迁移可用性、Redis ping、生产必需配置。全部必须项正常返回 200，否则 503；不检查外部模型。
- `GET /health/dependencies/`：需要登录且具有管理能力/超级用户；返回 PostgreSQL、Redis、配置和模型配置状态摘要。模型故障标记 `DEGRADED`，不改变 live/ready。
- 所有响应只给 `UP/DOWN/DEGRADED` 与安全原因码，不返回连接串、密码、主密钥或堆栈。

## 8. JSON 日志与脱敏

生产 Formatter 输出：`timestamp, level, service, environment, event, logger, message, request_id, trace_id`，按上下文可加入 `method, route, status_code, duration_ms, organization_id, workspace_id, user_id, knowledge_base_id, document_id, task_id, model_config_id, error_code`。

Request middleware 不记录 body、query、Cookie、Authorization、Prompt、文档正文或模型完整回答。路由标签使用 Django resolver route 模板。过滤器递归脱敏 `password, token, cookie, authorization, api_key, encrypted_api_key, secret`，并保留现有公开/应用/邀请 Token 正则。

## 9. Request ID 与 Trace 传播

- 仅接受 `[A-Za-z0-9._-]{1,64}` 的 `X-Request-ID`，否则生成 UUID hex；响应始终回传。
- Django middleware 用 `contextvars` 保存 request_id，创建 `http.request` Span，并把 trace_id 注入日志上下文。
- Celery dispatch 使用 W3C `traceparent`/`tracestate` headers 注入当前上下文；Worker task 从 headers 提取并创建 `celery.document.process` 子 Span。
- Service Span 只记录有限 ID、计数、状态、模型类型/安全 provider 名和降级布尔值，不记录正文。
- OTLP 未配置或 Collector 不可用时使用 no-op/批量导出失败日志，业务继续。

## 10. OpenTelemetry Span 模型

第一版落地高价值边界：

- HTTP：`http.request`。
- 文档：`task.enqueue`、`document.process`、`document.parse`、`document.chunk`、`embedding.batch`、`paragraph.replace`、`document.finalize`。
- RAG：`rag.prepare`、`query.embedding`、`retrieval.vector`、`retrieval.bm25`、`retrieval.rrf`、`retrieval.rerank`、`context.build`、`llm.chat`、`sse.first_token`、`conversation.persist`。
- Agent：保留执行层业务事件，本阶段在总入口 `agent.run` 与工具执行边界增加 Span。

每个 Span 可记录 duration（由 SDK 计算）、status/error_code、retry_count、candidate_count、selected_count、模型类型/provider、是否降级。不会虚构无法从 SDK 得到的 token 数。

## 11. Prometheus 指标字典与标签约束

实现任务书列出的 HTTP、SSE、文档任务、检索、模型与 Celery 语义指标。Counter 用于累计事件，Gauge 用于当前连接/任务/依赖状态，Histogram 用于延迟和队列等待。

允许标签仅限：`method, route, status_class, result, task_type, task_stage, model_type, provider, retrieval_strategy, error_code`，其中值均经过白名单归一化。禁止 request_id、trace_id、用户名、文档/会话/组织 ID、完整 URL、模型名和用户问题。HTTP 桶：5ms～10s；SSE/模型/任务桶按 100ms～10min 分层。标签测试会检查采样文本中不存在动态 ID。

## 12. Grafana 仪表盘

Provisioning 自动加载三个入库 Dashboard：

1. 系统总览：QPS、2xx/4xx/5xx、P50/P95/P99、SSE 连接、实例/依赖健康。
2. RAG 与模型：检索各阶段延迟、候选量、Reranker 降级、模型结果/超时/限流、首 Token、无答案。
3. Celery 与文档：Worker 心跳、队列深度、最老任务、活跃/成功/失败/重试/僵尸、处理 P95。

## 13. 告警规则

提供 BackendDown、HighHttp5xxRate、HighHttpP95Latency、NoCeleryWorkers、CeleryQueueBacklog、CeleryOldestTaskTooOld、DocumentTaskFailureRateHigh、StaleProcessingTasks、ModelTimeoutRateHigh、ModelRateLimited、DatabaseUnavailable、RedisUnavailable。每条均含 `for`、summary、description 和 Runbook anchor。阈值是首版配置值，真实压测后再校准，不称为实测 SLO。

## 14. SLI、初始 SLO 与错误预算

- 核心 API 成功率：成功请求/纳入统计的核心 API 请求；目标 99.9%。
- 普通 CRUD P95：不含 SSE、上传传输和外部模型；目标 500ms。
- 检索 P95：从检索开始到证据完成，不含生成；目标 1s。
- SSE 首 Token P95：从请求进入到第一个非空 content；目标 3s。
- 文档任务最终成功率：SUCCESS/(SUCCESS+FAILURE)，用户取消不纳入；目标 99%。
- 队列等待：95% 任务 60s 内从 PENDING 到 PROCESSING。

错误预算以 30 天为建议窗口：允许失败比例 = 1-SLO。没有真实生产窗口前仅展示计算方式。

## 15. Celery 恢复与僵尸任务策略

- `updated_at` 是任务最后数据库心跳；进度回调和状态迁移都会更新。
- Worker 周期心跳写 Redis 带 TTL，Prometheus 用于在线信号，不作为任务审计事实。
- Beat 每分钟执行幂等收敛器。任务满足“ACTIVE 且 updated_at 早于 `max(硬超时+60s, 配置阈值)`”才视为僵尸，避免误判正常长任务。
- 收敛器锁定任务行并二次检查时间；仍旧僵尸则标记 FAILURE/FAILED，错误码为安全固定文本。上传且没有 Paragraph 的 Document 设 FAILURE；重处理或已有 Paragraph 时保留旧切片和可用状态。
- 不自动无限创建重试任务。Celery 单任务最多 3 次指数退避+抖动；僵尸任务由用户/运维显式重试。
- Beat 重复执行只会处理仍处于 ACTIVE 的行，因此幂等。Redis 锁过期不破坏 Paragraph；DocumentProcessor 的事务替换继续保证重复执行不追加切片。

## 16. 备份恢复方案

- 数据库：使用 `pg_dump --format=custom` 写入明确的 `stage15-` 文件；恢复到独立 `knowledge_chat_stage15_restore` 临时数据库并执行计数/关联校验。
- 文件：数据库备份不包含 media Volume。运行手册单独提供 media 打包与校验步骤；恢复必须将数据库与对应时点文件一起处理。
- 演练只创建/删除 `stage15` 临时数据库和临时数据，不覆盖当前数据库。
- 初始设计目标：数据库 RPO 24h、RTO 4h；这不是承诺。真实演练记录备份大小、耗时、恢复耗时和逐实体计数后，才能报告本次结果。

## 17. 压测口径

- A 普通 API：10/25/50/100 并发，客户端 5s；记录样本、成功、失败、超时、吞吐、P50/P95/P99。
- B SSE：Mock 模型，5/10/20/50 并发；首 Token 10s、总请求 120s；分别记录首 Token 与总耗时。
- C 异步上传：10/25/50/100/200 并发，小型文本，提交 10s；分开记录 HTTP 202、入队、最终成功、排队和处理耗时。

报告必须带时间、Git/dirty 状态、CPU/内存、Worker/线程、数据库/Redis、Mock 参数、原始 CSV/JSON。HTTP 202 不等于最终处理成功。

## 18. 故障演练矩阵

| 场景 | 预期 | 数据安全边界 |
| --- | --- | --- |
| Worker 停止/崩溃 | Worker 指标下降，任务等待或被收敛 | 旧 Paragraph 不删，恢复后可显式重试 |
| Redis 暂停 | ready=503，投递返回安全失败 | 数据库记录可审计，不假装已入队 |
| PostgreSQL 暂停 | ready=503，API安全失败 | live仍响应，容器不无限重启 |
| Mock 模型超时/429 | SSE安全 error，模型指标/Span失败 | 不保存空 AI 消息，不记录密钥 |
| Reranker 超时 | 回退融合结果并计数 | 用户仍获得可解释检索结果 |
| Nginx/Backend 重启 | 新请求短暂失败后恢复 | 已提交事务不回滚成半状态 |
| 幂等重处理重复提交 | 返回同一活动任务或拒绝冲突 | Paragraph替换而非追加 |

## 19. 安全威胁模型

| 威胁 | 控制 |
| --- | --- |
| metrics/Trace UI 暴露公网 | 无 Nginx 路由；监控 UI 仅 loopback |
| 上传文件被猜路径下载 | `/media`经Django租户鉴权，真实目录只允许Nginx internal location |
| 日志泄漏 Cookie/Key/Token | 不记录 Header/body/query；递归敏感键与 Token 正则脱敏 |
| 指标高基数耗尽 Prometheus | route模板和有限枚举标签；专项测试 |
| Trace泄漏 Prompt/正文 | Span属性白名单，不挂载业务内容 |
| 伪造超长 Request ID污染日志 | 64字符正则，否则重新生成 |
| 健康检查成为重启风暴 | live不查依赖；容器只用live，ready用于流量门控 |
| 迁移竞态 | 单独 migrate one-shot，Web不自动 migrate |
| Beat重复 | Compose只启动一实例；收敛器数据库条件更新幂等 |
| 遥测后端拖垮业务 | Batch exporter短超时/no-op降级，指标异常吞掉 |
| 备份覆盖真实库 | 恢复脚本要求 `stage15` 命名并拒绝源库同名 |

## 20. CI/CD 规则

CI继续执行全量 Django 测试、迁移漂移、system check、Linux npm build，并增加生产 `check --deploy`、两类镜像构建、Compose config、Nginx `-t`、临时依赖启动、health/HTTP冒烟以及镜像内容检查。CI不推送公开镜像、不调用真实模型、不注入真实密钥。

## 21. 设计自审

- 不选择 Uvicorn/ASGI，因为现有核心链路是同步 SDK + 同步生成器；换运行器不会自动获得异步吞吐。gthread让多个SSE共享单进程线程，同时保留现有行为。
- 第一版 Backend 单进程是指标正确性与部署简单度的主动取舍；扩为多进程前必须启用 Prometheus multiprocess collector 或独立 exporter。
- live不依赖数据库/Redis，避免依赖抖动触发应用重启；ready才控制接流量。
- 模型不属于后台启动必需依赖；模型失败是 DEGRADED，不让管理后台整体下线。
- Redis心跳只用于观测，数据库状态仍是任务事实来源。
- 僵尸收敛不自动反复重试，避免依赖故障时重试风暴；旧切片保留。
- Nginx 不对外代理 metrics；这比在应用中依赖不稳定来源 IP 判断更清晰。
- 不引入 Loki、Kubernetes或对象存储，保持阶段边界；stdout JSON、单机 Compose 和共享 media Volume是当前规模的可交付方案。

## 22. 实际实施记录

已按垂直切片完成以下代码与配置交付：

1. **生产运行骨架**：新增后端 Gunicorn 镜像、前端多阶段构建/Nginx 镜像、PostgreSQL、Redis、Worker、Beat、OTel Collector、Prometheus、Grafana 与 Jaeger 的生产 Compose；迁移作为显式 one-shot 操作，不在每个 Web 实例启动时并发执行。
2. **入口与静态资源**：Nginx 统一代理 SPA、API 与 SSE；静态哈希资源长期缓存，`index.html` 禁止长期缓存；SSE 路径关闭代理缓冲。上传文件改为 Django 鉴权后通过 `X-Accel-Redirect` 交给 Nginx 内部 location，避免直接暴露 media 目录。
3. **健康与可观测性**：实现 live、ready、受权限保护的依赖详情和不对公网代理的 Prometheus 指标端点；生产日志改为 JSON，并为 HTTP、Celery、文档处理、检索、模型与 Agent 链路加入 Request ID、Trace 上下文、有限标签指标和关键 Span。
4. **异步恢复**：Worker 心跳写入带 TTL 的缓存；Beat 周期扫描僵尸任务；收敛过程行锁后二次检查，保留重处理前仍可用的 Paragraph，且不会无限自动重试。
5. **监控与运维**：Provisioning 三个 Grafana Dashboard、十二条 Prometheus 告警及对应 Runbook；新增安全的 PostgreSQL 备份/临时恢复脚本、压测脚本与任务最终状态统计工具。
6. **CI**：继续执行后端全量测试、迁移漂移、Django 检查、前端类型检查和 Linux 生产构建，并增加 Compose 校验、镜像构建、Nginx 配置检查、镜像敏感文件检查、迁移和 HTTP 冒烟。

开发机没有安装官方 OpenTelemetry SDK 与 Prometheus Client 时，兼容层仅保证本地启动和自动化测试不被阻断；生产依赖文件明确固定官方包，Docker/CI 安装失败会直接使镜像构建失败，不会以兼容实现冒充生产遥测。

## 23. 验证证据

2026-09-12 在当前工作区执行：

- `py -3.10 manage.py test`：154 项全部通过，最终回归耗时 111.281 秒；其中 `api.test_stage15` 15 项全部通过。
- `py -3.10 manage.py makemigrations --check --dry-run`：`No changes detected`。
- `py -3.10 manage.py check`：0 issue；设置生产环境变量后 `manage.py check --deploy`：0 issue。
- `npm run type-check`：通过。
- `docker compose ... config --quiet`：生产 Compose 单文件及本地回环覆盖组合均通过。
- Grafana Dashboard JSON、部署 YAML、Prometheus/Grafana provisioning YAML 与 CI YAML：基础解析通过。
- `python -m compileall -q api config` 与 `git diff --check`：通过；仅出现既有 Windows CRLF 提示，无空白错误。
- 敏感文件检查：未发现被跟踪的真实 `.env`、数据库、media 或备份文件；模式扫描只命中 `test_stage15.py` 中明确的临时测试密钥。

以下项目**没有在当前环境宣称通过**：

- `npm run build` 在 Windows 主机执行到 Vite 加载配置时失败，原因是本机禁止创建 esbuild 子进程（`spawn EPERM`）；`vue-tsc` 已在同一命令中通过，Linux CI/容器生产构建仍待实际运行确认。
- `docker version` 的客户端信息正常，但连接 `DockerDesktopLinuxEngine` 返回 `permission denied`；因此未执行镜像构建、Nginx `-t`、真实容器健康检查、SSE 反代、Prometheus target、Grafana/Jaeger 页面、故障演练、备份恢复和三类压测。
- 当前主机无法从 PyPI 完成官方遥测依赖安装；依赖已经写入 `requirements.txt`，需由 Docker/CI 联网构建验证。

所以本阶段代码与静态配置已交付，但在完成上述真实容器验收前，阶段状态是“**实现完成，运行验收受环境阻塞**”，不是完整生产验收通过。

## 24. 已知限制

- 当前 Codex 进程无 Docker Engine访问权限；容器内 Linux 标准构建和真实拓扑可能需要在 Docker Desktop授权环境补验。
- 当前 Windows 主机策略阻止 Vite 启动 esbuild 子进程；未修改 `node_modules`，也未用 WASM 构建绕过生产标准链路。
- 本机未能安装官方 OpenTelemetry/Prometheus Python 包，开发兼容层不提供完整生产遥测语义；生产镜像必须成功安装 `requirements.txt` 中的官方实现。
- 单机共享 media Volume不是跨主机高可用文件存储；数据库备份不自动包含文件。
- JSON向量和Python扫描限制大规模检索吞吐，本阶段不迁移 pgvector。
- Gunicorn gthread上的同步SSE受线程数约束；规模扩大后需单独进行 async/ASGI改造和连接容量测试。
- 未接入 Alertmanager通知渠道；规则可在Prometheus中验证。

## 25. 人工补验清单

若当前环境仍不能访问 Docker Engine，应在普通 PowerShell 中按运行手册执行：Compose config → build → migrate → up → Nginx/health/SSE冒烟 → Prometheus targets → Grafana provisioning → Jaeger Trace → 故障演练 → 临时备份恢复 → 压测 → stage15清理，并保留原始输出与报告。
