# 生产运维手册

## 发布前检查

1. 使用独立生产密钥填写 `.env.production`，禁止沿用示例值。
2. 配置 HTTPS 域名、可信 Host、CSRF Origin、数据库和 Redis 凭据。
3. 执行后端测试、迁移检查、前端类型检查和生产构建。
4. 验证 Compose 渲染结果，不在终端或流水线输出完整环境变量。
5. 完成数据库备份并记录恢复点。

```powershell
docker compose -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml --profile ops run --rm migrate
docker compose -f docker-compose.production.yml up -d
```

## 健康检查

| 接口 | 判断 |
| --- | --- |
| `/health/live/` | Web 进程能够响应 |
| `/health/ready/` | 服务已完成启动且可接收流量 |
| `/health/dependencies/` | 数据库、Redis 等依赖状态；仅授权访问 |
| `/internal/metrics/` | Prometheus 指标；仅内部网络访问 |

发布后至少验证登录、知识库读取、异步任务入队、Worker 消费、SSE 回答和引用恢复。就绪检查失败时不要继续导入生产流量。

## 监控重点

- HTTP 请求量、错误率、P50/P95/P99 延迟。
- 数据库连接、慢查询、锁等待和磁盘空间。
- Celery 队列深度、任务等待时间、失败率、重试率和心跳。
- 文档处理时长、僵尸任务、向量回填进度与双写失败。
- 模型调用延迟、超时、限流和 Reranker 降级率。
- SSE 活跃连接、首 Token 延迟与异常中断。

告警必须携带环境、服务、严重级别、关联仪表盘和处理手册链接。敏感字段在采集前完成脱敏。

## 常见故障处理

### Web 就绪失败

检查迁移状态、数据库连接和 Redis 连通性。若新版本引入兼容问题，停止向新实例导流并回滚镜像；不要直接删除数据库卷。

### 任务队列积压

确认 Worker 心跳、队列路由、Redis 和外部模型状态。扩容前先排除持续失败任务和锁泄漏，避免扩大重试风暴。

### 模型请求异常

按认证失败、模型不存在、限流、超时和网络故障分类。切换配置或供应商属于受控变更，应保留审计记录；不得在日志中打印密钥或请求头。

### pgvector 质量或延迟回退

比较当前 EmbeddingSpace、索引参数、执行计划和 Shadow 指标。可立即把读取模式切回 Legacy；索引重建和旧字段清理需要变更窗口。

## 备份与恢复

- 定期备份 PostgreSQL，并把备份复制到与运行主机隔离的位置。
- 加密主密钥、应用密钥和备份密钥使用独立密钥管理策略。
- 恢复必须先进入隔离数据库，校验 schema、行数、关键关联和抽样文件，再决定切换。
- 上传文件或对象存储需要与数据库恢复点协调，避免元数据与物理文件不一致。

## 变更边界

允许值班人员直接执行：查看日志和指标、重启无状态实例、扩容 Worker、切换到已验证的降级读取路径。

需要审批：删除数据或卷、回滚数据库迁移、强制解除任务锁、重建大索引、轮换生产密钥、恢复备份或修改公网安全策略。

