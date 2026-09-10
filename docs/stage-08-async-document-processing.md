# 第八阶段设计：Redis + Celery 异步文档处理与并发验证

## 1. 现状审计与基线

### 1.1 当前链路

当前 `DocumentListView.post()` 和 `DocumentReprocessView.post()` 都在 Django HTTP 请求中直接调用 `process_document()`：

```text
请求线程 → 读文件 → 提取文本 → 切片 → Embedding → 事务替换 Paragraph → 返回
```

`process_document()` 已实现“先准备新数据，后在事务中替换”的安全重处理；失败且有旧 Paragraph 时恢复 Document.SUCCESS 并保留旧数据。Document 删除后的物理文件由 `post_delete + transaction.on_commit` 清理。前端上传、重处理期间只显示单个按钮 loading，没有跨刷新任务状态。

### 1.2 数据和状态边界

- `Document.status` 只有 `PROCESSING / SUCCESS / FAILURE`，表达文档当前是否可用。
- 没有任务实体、进度、重试次数、Celery ID、取消状态或任务历史。
- 没有 Redis、Celery、PostgreSQL或Locust依赖。
- SQLite 是默认数据库，适合本地学习与自动化测试，不作为多 Worker 并发压测数据库。
- Agent 工具是短时同步只读/纯计算，不在本阶段迁移到 Celery。

### 1.3 基线结果（2026-08-25）

- `py -3.10 manage.py test`：54项通过。
- `py -3.10 manage.py check`：0 issues。
- Migration `0001—0004` 已应用，`makemigrations --check --dry-run` 无变化。
- `npm run type-check`：通过。
- `npm run build`：通过；保留既有第三方 PURE 注释与单 chunk 大于500 kB警告。
- Docker CLI 29.1.3和Compose存在；首次检查时Desktop Engine未运行，用户随后启动，复核Server 29.1.3可用。
- 基线时本机没有Redis/Celery/psycopg/Locust；实施阶段新增固定版本依赖，并通过独立Compose拉起真实服务。

基线结果只用于说明改造前状态；真实Redis/Worker/PostgreSQL/200并发结论见第18节，不以eager测试替代。

## 2. 范围与非范围

### 2.1 本阶段包含

1. DocumentProcessingTask数据库状态机与审计。
2. Celery JSON任务、Redis Broker和文档级分布式锁。
3. 异步上传与异步重新处理。
4. 事务提交后入队、Broker失败可见和手动重试。
5. 进度、重试、取消、幂等与删除竞态。
6. 任务列表/详情/重试/取消API与owner隔离。
7. Vue任务状态、轮询、进度和防重复交互。
8. SQLite/eager自动化测试、真实Redis/Worker冒烟和Locust脚本。
9. 可选、独立的PostgreSQL临时Compose压测环境，不迁移真实SQLite数据。

### 2.2 本阶段不包含

MCP、RRF、Cross-Encoder Reranker、pgvector、OCR、对象存储、WebSocket、Kubernetes、自动扩缩容、Agent外部副作用工具、RBAC和无关UI重构。

## 3. Redis、Celery与数据库职责

| 组件 | 职责 | 不承担的职责 |
|---|---|---|
| Django/数据库 | Document与Task业务状态、owner权限、幂等约束、最终事务 | 不执行后台任务 |
| Redis | Celery Broker、短期协调、带token/TTL的文档锁 | 不是页面任务状态的唯一来源 |
| Celery Worker | 获取task_id、执行Service、进度/重试/终态收敛 | 不接收文件正文、Model对象或密钥 |
| Vue | 显示Document和Task、轮询活跃状态、防重复操作 | 不自行推断后台成功 |

数据库是最终业务事实来源。Celery Result Backend可以配置，但前端只查询项目Task API，避免Redis结果过期后丢失审计。

## 4. 数据模型

新增 `DocumentProcessingTask`：

- `document`：CASCADE，任务生命周期随Document结束。
- `task_type`：`UPLOAD / REPROCESS`。
- `status`：`PENDING / PROCESSING / RETRYING / SUCCESS / FAILURE / ENQUEUE_FAILED / CANCEL_REQUESTED / CANCELLED`。
- `celery_task_id`：Celery内部标识，只用于调度/撤销，不作为API权限凭证。
- `progress`：0—100。
- `current_stage`：`WAITING / READING / SPLITTING / EMBEDDING / SAVING / DONE / FAILED / CANCELLING / CANCELLED`。
- `attempt_count`：同一Task的实际处理尝试次数。
- `idempotency_key`：可选请求键；相同owner/知识库键只返回原任务。
- `error_message`：安全、脱敏、最大500字符。
- `created_at / started_at / finished_at / updated_at`。

owner和knowledge_base通过Document关联，不冗余存储，避免字段不一致。删除Document后不保留任务审计是当前个人项目的明确取舍；未来如需合规长期审计再增加不可变快照表。

数据库约束：

1. progress范围0—100。
2. 同一Document在 `PENDING/PROCESSING/RETRYING/CANCEL_REQUESTED` 中最多一条活跃任务（条件唯一约束）。
3. `(document, idempotency_key)` 在非空键上唯一。
4. 默认按 `-created_at,-id` 排序。

## 5. Document与Task状态机

### 5.1 Task

```text
创建 → PENDING
PENDING → PROCESSING | ENQUEUE_FAILED | CANCEL_REQUESTED
PROCESSING → SUCCESS | RETRYING | FAILURE | CANCEL_REQUESTED | CANCELLED
RETRYING → PROCESSING | FAILURE | CANCEL_REQUESTED
ENQUEUE_FAILED → PENDING（手动重试重新入队）
CANCEL_REQUESTED → CANCELLED
```

SUCCESS、FAILURE、CANCELLED是终态，普通重复投递不重新执行SUCCESS任务。FAILURE手动重试创建新的Task；ENQUEUE_FAILED尚未开始处理，可复用同一Task重新入队。

### 5.2 初次上传

```text
Document.PROCESSING + Task.PENDING
成功：Document.SUCCESS + Task.SUCCESS
失败：Document.FAILURE + Task.FAILURE
```

### 5.3 重新处理

如果原Document有旧Paragraph，重新处理排队和执行期间Document继续保持SUCCESS，Task表达进度。失败保留旧Paragraph、旧paragraph_count和旧embedding_signature；成功才事务替换。如果旧signature和当前配置不匹配，旧数据保留但检索继续排除，直到成功重处理。

## 6. 入队事务与Broker失败

上传/重处理Service在 `transaction.atomic()` 中创建Document（上传时）和Task，并使用 `transaction.on_commit()` 调用统一dispatch函数。这样回滚时不会发送幽灵任务。

dispatch只传 `task.id`：

```text
commit → process_document_task.apply_async(args=[task_id]) → 保存celery_task_id
```

Broker异常由dispatch捕获并安全映射：Task置 `ENQUEUE_FAILED`、stage置FAILED、保存用户可理解错误。初次上传Document置FAILURE；已有可用Paragraph的重处理Document保持SUCCESS。API返回503及Document/Task，不能声称已入队。

测试eager模式为避免Django TestCase外层事务吞掉on_commit，可以通过明确测试设置同步执行dispatch；生产异步模式必须只在on_commit后发送。

## 7. Celery可靠性与重试

- JSON serializer/accept_content，禁止Pickle。
- `acks_late=True`、`task_reject_on_worker_lost=True`、`worker_prefetch_multiplier=1`。
- soft time limit 300秒，hard time limit 360秒；锁TTL默认420秒。
- task参数只有整数task_id。
- TIMEOUT、RATE_LIMITED、CONNECTION_FAILED、临时数据库/Redis异常可重试。
- 文件格式、空文件、扫描PDF、认证/配置错误和校验错误不可重试。
- 最多3次，指数退避并带jitter；重试前Task置RETRYING、attempt_count保留。
- `acks_late`可能重复投递，因此执行器先检查终态并依赖锁、唯一约束和提交前复查保持幂等。

## 8. 分布式锁

锁Key：`knowledge-chat:document-processing:{document_id}`。

生产使用redis-py Lock：随机token、非阻塞获取、TTL、Lua所有权校验释放。测试/eager使用进程内锁实现同一接口，仅用于无外部依赖测试，不代表跨进程能力。

Redis锁是第一道运行时协调，数据库活跃Task唯一约束是第二道提交保护，最终 `select_for_update` 与Task/Document复查是第三道保护。任何一层都不能单独替代其他层。

## 9. 文档处理Service改造

`process_document()`继续是唯一解析/切片/Embedding/安全替换实现，增加可选：

- `progress_callback(stage, progress)`；
- `should_cancel()`；
- 重新处理时保持可用Document状态；
- 提交前 `select_for_update` 重新读取Document并检查取消。

检查点位于读文件前、提取后、切片后、Embedding前后和事务提交前。取消抛出专用异常，不当作普通处理失败，也不删除旧Paragraph。

## 10. 幂等与删除竞态

三层幂等：前端loading；数据库活跃Task唯一约束；Worker终态/锁/提交前复查。

删除Document：先把活跃Task置CANCEL_REQUESTED并尽力revoke排队任务，再删除Document。CASCADE删除Task；Worker后续读取不到Task/Document即安全退出。最终写入事务必须重新锁定Document，不能使用早先内存对象重新创建任何数据。

`revoke()`只是减少排队执行机会，不能作为强制终止保证。真正安全性来自协作检查与最终事务复查。

## 11. API契约

```http
GET /api/knowledge-bases/{knowledge_id}/processing-tasks/?page=1&page_size=20&status=PROCESSING
GET /api/knowledge-bases/{knowledge_id}/processing-tasks/{task_id}/
POST /api/knowledge-bases/{knowledge_id}/processing-tasks/{task_id}/retry/
POST /api/knowledge-bases/{knowledge_id}/processing-tasks/{task_id}/cancel/
```

列表按Document→KnowledgeBase→owner过滤；错误知识库、跨用户和不存在统一404。Task输出白名单字段，不返回Redis URL、绝对路径、traceback、API Key或原始Celery异常。

上传和重处理成功入队返回HTTP 202：

```json
{"code":202,"message":"文档已进入处理队列","data":{"document":{},"task":{}}}
```

Broker不可用返回503，但data仍提供已经保存的Document与ENQUEUE_FAILED Task供用户重试。

## 12. 前端交互与轮询

API类型增加Task、异步Document响应和任务分页。KnowledgeDetailView维护：

- `processingTasks / taskLoading / retryingTaskId / cancellingTaskId`；
- 2秒轮询最近任务；
- 无活跃Task停止；组件卸载或通过列表切换知识库时停止旧页面轮询；
- request sequence阻止过期响应覆盖新状态；
- 终态后刷新Document、模型状态和切片抽屉；
- 上传只等待202，不等待完整处理；
- 活跃任务禁用重处理，允许取消；
- FAILURE/ENQUEUE_FAILED显示安全原因和重试入口。

任务仅在知识库详情页复用，采用页面Composable或页面内状态，不强行引入全局Pinia。

## 13. 权限与并发威胁模型

| 威胁 | 控制 |
|---|---|
| 跨用户task_id | Task查询同时限定document__knowledge_base__owner和knowledge_id |
| task_id作为凭证 | Celery ID不用于API授权 |
| 事务回滚后幽灵任务 | transaction.on_commit |
| Broker异常静默丢失 | ENQUEUE_FAILED + 503 + 手动重试 |
| 重复点击/重复投递 | UI、条件唯一约束、Worker终态检查 |
| 多Worker同时处理 | Redis token锁 + DB约束 + select_for_update |
| 错误释放他人锁 | redis-py token/Lua所有权校验 |
| 删除后Worker写回 | CASCADE、协作取消、提交前重查/行锁 |
| 重处理失败破坏旧数据 | 新数据全部准备成功后事务替换 |
| Redis泄漏密钥/正文 | task payload仅task_id，JSON序列化 |
| 异常泄漏内网/密钥 | 安全错误映射，不返回str(exc)/traceback |
| Worker崩溃永久PROCESSING | late ack重投；启动/运维收敛超时任务列为已知边界 |

## 14. Docker与数据库

保留SQLite本地开发和测试。新增阶段Compose，Redis仅在内部网络；PostgreSQL使用全新临时卷/数据库，绝不自动搬运现有SQLite。Compose环境显式设置异步模式和Worker并发。若Docker引擎不可用，只记录未执行，不伪造真实验收。

## 15. 自动化测试矩阵

最终共69项测试，其中Stage 8新增15项，覆盖：202契约、rollback不入队、Broker失败、task_id payload、进度、初次/重处理成功失败、幂等请求/重复投递、手动重试竞态、自动重试上限与jitter、不可重试错误、取消、删除级联、owner、敏感字段、N+1和锁互斥。默认使用临时MEDIA_ROOT、eager Celery和进程内测试锁，不访问真实模型或网络；真实Redis/Worker/PostgreSQL另做集成验收。

## 16. 压测方案与判定

Locust只上传小型可识别文本并使用本地Embedding。预热10/50并发；正式200虚拟用户在10秒内各提交一次，客户端超时30秒。记录总量、成功/失败/超时、P50/P95/P99/max、RPS、队列长度、Worker并发、DB错误、CPU/内存及最终Document/Task数量。

目标：超时率0、HTTP错误率<1%、P95<1秒、P99<3秒。它们是目标而非预设结论；只有原始报告满足才能写入简历。“200并发提交”不等于200个文档同时完成，Worker吞吐单独记录。

## 17. 设计自审

1. 独立Task避免把过程状态塞进Document并破坏旧切片可用语义。
2. owner不冗余，防止Task与Document权限数据不一致。
3. Redis锁不替代数据库约束，因为锁可能过期、Redis可能重启。
4. 数据库约束不替代Redis锁，因为多个Worker仍可能重复做昂贵Embedding。
5. on_commit保证Worker看得到已提交行。
6. 失败重处理保留旧数据和旧signature；新配置下仍按现有规则排除不兼容向量。
7. 取消采用协作式检查，不宣称revoke可以安全杀死任意执行点。
8. JSON payload仅task_id，避免文件/密钥进入Broker。
9. 页面使用轮询而非WebSocket，范围更小且任务更新频率低。
10. SQLite只承担本地/eager验证；多Worker和200并发结论只来自PostgreSQL临时环境。
11. 不新增django-celery-results，避免和项目Task模型重复职责。
12. 不把Docker不可用时的模拟测试写成真实Redis/Worker通过。

## 18. 实际实施与验证记录

### 18.1 实施清单

- 新增Migration `api/migrations/0005_documentprocessingtask.py`，由Django正常生成并在SQLite与临时PostgreSQL中应用。
- 新增 `config/celery.py`、`api/tasks.py`、`services/document_tasks.py`、`services/document_task_lock.py`。
- `document_processor.py`成为上传与重处理共用入口，并以32个切片为一批检查取消、报告Embedding进度。
- 上传/重处理改为HTTP 202；新增任务列表、详情、重试和取消API。
- Vue知识库详情页增加任务进度、阶段、尝试次数、重试、取消和2秒轮询；终态自动刷新Document与模型状态。
- 新增 `Dockerfile.stage08` 与 `docker-compose.stage08.yml`。Redis/PostgreSQL不发布端口，后端只绑定 `127.0.0.1:18000`；Worker使用UID 10001非root运行；密码和临时Token来自被Git忽略的`.env.stage08`，仓库只保留example。
- 新增 `loadtests/stage08_locustfile.py`、冒烟/竞态脚本、临时数据命令及原始CSV。
- Stage 8环境使用Gunicorn 4 workers × 4 threads；早期runserver预热连接断开结果保留，不作为最终结论。

### 18.2 自动化与构建

2026-08-26最终结果：

- `py -3.10 manage.py test`：69项通过。
- `py -3.10 manage.py check`：0 issues。
- `py -3.10 manage.py makemigrations --check --dry-run`：No changes detected。
- `npm run type-check`：通过。
- `npm run build`：通过，1678 modules；保留既有第三方PURE注释和单chunk约1.17MB警告。

### 18.3 真实Redis/Celery/PostgreSQL

- Compose四个核心服务均启动并健康；Redis `PING`返回`PONG`。
- Worker确认连接 `redis://redis:6379/0`、concurrency=4，且注册 `api.tasks.process_document_task`。
- 运行配置确认：JSON task/result serializer、`accept_content=['json']`、late ack、worker lost重投、prefetch=1。
- 最终真实HTTP冒烟：上传72.79ms返回202，观察到 `PENDING → SUCCESS`，进度100、切片1。
- PostgreSQL验收发现并修复SQLite未暴露的问题：`select_for_update + nullable LEFT JOIN`不受PostgreSQL支持；最终仅锁Document自身。
- 真实Redis锁验证：首个持有者成功、第二持有者被拒绝、持有者释放后可重新获取。

### 18.4 删除与取消竞态

停止Worker后通过真实HTTP创建两条排队任务：

1. task 364取消后为CANCELLED、进度0、切片0；Worker恢复消费消息后仍返回CANCELLED。
2. document/task 365排队时删除；Worker恢复后消息安全返回MISSING，Document、Task、Paragraph均不存在，物理文件不存在。

这证明`revoke`之外，数据库状态检查、CASCADE和最终重查能够阻止Worker写回已删除资源。

### 18.5 浏览器验收

在 `http://127.0.0.1:5174` 连接独立Docker后端完成：登录、创建 `stage08-browser-kb`、上传TXT、显示处理成功/进度/尝试次数、切片抽屉、异步重处理、刷新恢复、检索问答与引用。页面刷新后任务终态仍存在，控制台无错误。由于本地哈希处理很快，页面未稳定捕获每一个中间阶段；中间状态由真实HTTP轮询和任务数据库记录补验。

### 18.6 Locust并发结果

最终原始文件：`loadtests/results/burst-200_stats.csv`、`burst-200_stats_history.csv`、`burst-200_failures.csv`。

正式条件：200用户、20 users/s（10秒爬升）、每用户一次唯一TXT上传、客户端超时30秒、本地确定性Embedding、PostgreSQL、Redis、Celery concurrency=4、Gunicorn 4×4。

| 指标 | 真实结果 | 目标 | 判定 |
|---|---:|---:|---|
| 上传请求 | 200 | 200 | 通过 |
| 成功/失败/超时 | 200 / 0 / 0 | 超时0、错误率<1% | 通过 |
| P50 | 230ms | 记录项 | 通过 |
| P95 | 470ms | <1s | 通过 |
| P99 | 610ms | <3s | 通过 |
| 最大响应 | 611.92ms | <30s | 通过 |
| 平均吞吐 | 20.99 req/s | 记录项 | 通过 |

压测期间资源快照：backend 26.33% CPU/280.7MiB，worker 0.05%/224MiB，PostgreSQL 4.51%/84.57MiB，Redis 4.61%/9.70MiB，Locust 17.03%/53.41MiB；快照时Redis队列长度0。压测前160个临时Document/Task，压测后360个，增量恰好200；360个任务全部SUCCESS、0失败、0活跃、0重复幂等键、360个Paragraph，正式时间窗无数据库/Worker错误。

### 18.7 安全复查与清理

- Redis/PostgreSQL无宿主机端口，Redis不对公网暴露；后端仅localhost端口。
- Worker改为非root；Celery禁止Pickle；Task API不输出celery_task_id、内部URL或traceback。
- 修复首次上传取消后Document长期PROCESSING问题：无旧切片时置FAILURE并提示已取消。
- 修复手动重试与活跃任务竞态下可能重复dispatch的问题。
- 未在Task payload、日志、API或前端状态中写入API Key、文件正文或Fernet密钥。
- 清理前临时MEDIA文件365个；执行受前缀保护的清理命令后，stage08用户/知识库/Document/Task/Paragraph均为0，MEDIA文件为0。
- 验收后停止临时Compose服务，但保留镜像、空卷和CSV；可按README命令重新启动。

## 19. 已知限制

- 协作取消只能在检查点停止；单次正在进行的外部Embedding HTTP调用需等待SDK超时。本地快速任务可能在用户点击取消前已经成功。
- Redis锁TTL按hard time limit设置；未来任务超过360秒时需要锁续租和定期心跳。
- 本阶段不实现定时扫描并自动收敛历史僵尸PROCESSING任务；当前依靠late ack、worker-lost重投、时间限制和人工重试。
- 默认Windows学习启动仍使用SQLite；多Worker和200并发结论只适用于本阶段独立PostgreSQL/Gunicorn环境。
- Locust结果证明200个HTTP提交，而不是200个耗时Embedding同时完成；处理吞吐取决于Worker并发、文档大小和外部模型延迟。
