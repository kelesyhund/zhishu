# 系统架构

## 产品边界

知枢面向企业技术文档检索、故障排查与知识问答。系统覆盖组织与工作空间隔离、文档摄取、混合检索、带引用回答、会话管理、模型配置、应用发布及运行审计。计费、企业身份目录同步和跨地域高可用不在当前版本范围内。

## 运行拓扑

```text
Browser
  │
  ▼
Nginx ──► Vue SPA
  │
  ▼
Django REST API ─────► OpenAI-compatible endpoints
  │       │
  │       ├──────────► Redis ─► Celery workers
  │       └──────────► OpenTelemetry / Prometheus
  ▼
PostgreSQL + pgvector
```

- Nginx 提供静态资源、反向代理和公开接口边界。
- Django REST Framework 负责认证、授权、资源契约与业务编排。
- PostgreSQL 保存租户数据、业务数据和向量；pgvector 提供数据库内相似度检索。
- Redis 用作缓存、任务代理和分布式协调；Celery 执行文档解析与向量化任务。
- Vue 3 单页应用提供企业工作台、知识库、模型、应用和运维视图。

## 后端边界

后端按职责拆分为 View、Serializer、Service 和 Model：

1. View 解析请求、限定资源作用域并组织统一响应。
2. Serializer 校验外部数据并控制可见字段。
3. Service 实现文档处理、检索、模型调用、权限和任务状态等业务规则。
4. Model 与数据库约束保证关联关系、唯一性和级联行为。

复杂处理不放在 View 中。模型密钥解密、外部端点校验、向量写入和检索策略均由独立 Service 负责，便于单元测试和故障隔离。

## 核心数据域

- **身份与租户**：User、Organization、Membership、Workspace、WorkspaceMembership。
- **知识资产**：KnowledgeBase、Document、Paragraph、EmbeddingSpace、ParagraphEmbedding。
- **问答与会话**：Conversation、Message、引用信息和检索调试记录。
- **模型与应用**：ModelConfig、Application、发布版本和公开访问凭证。
- **异步与审计**：处理任务、AgentRun、ToolExecution、AuditLog、VectorMigrationRun。

组织和工作空间构成租户边界；知识库、会话、应用及模型配置的查询都必须同时满足资源归属和角色权限。

## 关键调用链

### 文档摄取

```text
上传请求 → 权限检查 → Document 记录 → Celery 任务
→ 解析器 → 结构化/父子切片 → Embedding → PostgreSQL/pgvector
→ 状态、进度和签名更新
```

处理任务使用幂等键、租约与重试策略。重处理先生成新结果，再在事务内替换旧切片，避免失败时破坏可用数据。

### 检索问答

```text
问题 → 会话权限 → 查询预处理 → BM25 + Vector 候选
→ RRF/加权融合 → 可选 Reranker → 上下文预算
→ LLM SSE 输出 → 引用与消息持久化
```

Embedding 配置由知识库解析。文档签名与当前 Embedding 空间不一致时不会参与检索，并被标记为需要重新处理。

### 应用访问

后台会话身份与公开应用凭证是两个独立认证域。发布版本固定知识库和模型快照；草稿配置不会静默影响已发布版本。

## 部署模式

- 本地开发：启动 Django 与 Vite，允许使用内置降级能力。
- 单机生产：Docker Compose 运行 Nginx、Web、Worker、Beat、PostgreSQL、Redis 和可观测组件。
- 外部托管依赖：可替换为托管 PostgreSQL、Redis、对象存储和遥测后端，但需补充网络策略、备份和容量验证。

生产部署细节见 [运维手册](operations-runbook.md)，安全边界见 [安全与租户](security-and-tenancy.md)。

