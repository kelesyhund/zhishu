# 第十三阶段：组织、工作空间、RBAC 与审计

## 1. 现状审计

- 开发前基线（2026-09-10）：后端 `112` 项测试通过；`api` 迁移 `0001`～`0009` 已应用；`makemigrations --check --dry-run` 显示无变更。
- 前端 `npm run type-check` 通过；`npm run build` 在当前受限执行环境中因 `esbuild spawn EPERM` 未能启动构建进程，属于环境限制，不能表述为生产构建通过。
- `KnowledgeBase`、`ModelConfig`、`Application` 是当前三类根业务资源，均直接以 `owner` 作为访问边界。文档、切片、任务、会话、消息、应用版本和访问凭证均通过根资源间接归属。
- 内部 API 普遍使用 `owner=request.user`，没有组织、工作空间、成员角色、统一能力判断和切换上下文。
- 公开网页及 OpenAI 兼容 API 使用公开 Token、访客 Token或应用凭证，不依赖登录用户；这些入口必须继续与 `X-Workspace-ID` 解耦。
- 异步文档任务只传递 `processing_task_id`，Worker 会通过 `Task → Document → KnowledgeBase` 重新解析业务归属，不接收前端提供的工作空间 ID。
- 当前没有不可变的管理审计模型；应用访问日志是运行流量日志，不等同于组织治理审计。

## 2. 范围与非范围

本阶段增加组织、组织成员、工作空间、工作空间成员、工作空间上下文、能力型 RBAC、管理审计、成员/工作空间/审计界面，并将知识库、模型配置和应用迁移到工作空间边界。

不引入企业 SSO、LDAP、SCIM、细粒度资源 ACL、自定义角色编辑器、跨组织资源共享、审计导出、对象存储或新的消息基础设施。保留 `owner` 字段作为创建者和旧代码兼容信息，但不再将其作为新内部 API 的授权依据。

## 3. 数据模型

### Organization

- `name`、全局唯一 `slug`、`status`、`created_by`、`created_at`、`updated_at`。
- 状态为 `ACTIVE` 或 `DISABLED`。本阶段不提供组织物理删除 API，避免删除审计链。

### OrganizationMembership

- 关联 `organization` 与 `user`，角色为 `OWNER / ADMIN / MEMBER / AUDITOR`。
- `(organization, user)` 唯一。
- 最后一名 OWNER 不能被删除、降级或移出；判断在事务中锁定组织和成员行。

### Workspace

- 关联组织，字段为 `name`、组织内唯一 `slug`、`status`、`is_default`、`created_by` 和时间字段。
- 组织至少保留一个默认工作空间。被根资源引用时数据库 `PROTECT`，API 返回 409。

### WorkspaceMembership

- 关联工作空间与用户，角色为 `ADMIN / DEVELOPER / OPERATOR / VIEWER / AUDITOR`。
- `(workspace, user)` 唯一。组织 OWNER/ADMIN 无需重复记录即可获得工作空间 ADMIN 能力；组织 AUDITOR获得只读审计能力。

### AuditEvent

- `organization`、可空 `workspace`、可空 `actor`、`action`、`resource_type`、`resource_id`、`result`、`request_id`、`ip_hash`、安全 `metadata`、`created_at`。
- 业务 API 只允许查询，不提供新增、修改和删除端点。记录服务使用字段白名单和长度上限，禁止密钥、Token、Authorization、原始提示词和完整文档内容进入 metadata。
- IP 使用服务端 `AUDIT_IP_HASH_KEY` 的 HMAC-SHA256；未配置时回退 Django `SECRET_KEY`，生产部署必须单独配置。

### 根资源归属

- `KnowledgeBase.workspace`、`ModelConfig.workspace`、`Application.workspace` 为非空外键并使用 `PROTECT`。
- `ModelConfig` 和 `Application` 的名称唯一约束从 `(owner, name)` 改为 `(workspace, name)`。
- 模型配置绑定、应用绑定知识库均必须工作空间一致。

## 4. 数据迁移与回滚

迁移顺序：

1. 创建组织、成员、工作空间、成员和审计表。
2. 给三类根资源增加可空 `workspace`。
3. `RunPython` 为每个已有用户创建“个人组织/默认工作空间”，赋予组织 OWNER 与工作空间 ADMIN，并把该用户现有根资源批量归入默认工作空间。
4. 校验不存在空归属后，把根资源 `workspace` 改为非空。
5. 替换模型配置和应用的唯一约束。

迁移不修改文件、切片、会话、消息、版本、凭证、Token 或主外键链。反向迁移先恢复旧唯一约束，再移除工作空间字段和治理表；若两个工作空间中同一创建者存在同名资源，旧唯一约束无法恢复，因此生产回滚前必须先运行同名冲突检查并备份数据库。

## 5. 工作空间上下文协议

- 登录后调用 `GET /api/me/context/` 获取可访问组织、工作空间、有效角色和能力。
- 内部业务请求通过 `X-Workspace-ID` 传递当前工作空间。
- 无 Header 时：只有一个可访问工作空间则兼容回退；多个工作空间返回 `400 / WORKSPACE_CONTEXT_REQUIRED`，禁止猜测。
- Header 非整数、工作空间不存在、已禁用或用户无成员关系，统一返回 404，避免枚举租户。
- `/health/`、`/login/`、`/public/**`、`/v1/**` 不要求此 Header。公开入口从发布应用反查固定工作空间，不接受调用方覆盖。
- 前端只持久化非敏感的工作空间 ID。退出登录清除；收到上下文 404/400 后刷新上下文并回到可用默认空间。

## 6. RBAC 能力模型

视图只声明能力，不直接比较角色字符串。能力映射集中在 `workspace_permissions.py`：

| 能力 | ADMIN | DEVELOPER | OPERATOR | VIEWER | AUDITOR |
| --- | --- | --- | --- | --- | --- |
| `workspace.read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `knowledge.read/chat` | ✓ | ✓ | ✓ | ✓ | 仅 read |
| `knowledge.write` | ✓ | ✓ |  |  |  |
| `document.process` | ✓ | ✓ | ✓ |  |  |
| `model.read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `model.manage` | ✓ | ✓ |  |  |  |
| `application.read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `application.write` | ✓ | ✓ |  |  |  |
| `application.operate` | ✓ | ✓ | ✓ |  |  |
| `member.manage` | ✓ |  |  |  |  |
| `audit.read` | ✓ |  |  |  | ✓ |

组织 OWNER/ADMIN 映射为工作空间 ADMIN；组织 AUDITOR 映射为 AUDITOR。组织 MEMBER 使用显式工作空间成员角色；没有工作空间成员关系则不可访问。

错误语义：没有租户可见性返回 404；已是该工作空间成员但能力不足返回 403；参数错误返回 400；删除被引用资源或最后所有者冲突返回 409。

## 7. API 契约

- `GET /api/me/context/`
- `GET /api/organizations/`，`POST /api/organizations/`
- `GET/PATCH /api/organizations/{id}/`
- `GET/POST /api/organizations/{id}/members/`
- `PATCH/DELETE /api/organizations/{id}/members/{membership_id}/`
- `GET/POST /api/organizations/{id}/workspaces/`
- `GET/PATCH/DELETE /api/organizations/{id}/workspaces/{workspace_id}/`
- `GET/POST /api/workspaces/{workspace_id}/members/`
- `PATCH/DELETE /api/workspaces/{workspace_id}/members/{membership_id}/`
- `GET /api/audit-events/?action=&actor_id=&result=&date_from=&date_to=&page=&page_size=`

所有 JSON 端点继续返回 `{code, message, data}`。列表服务端分页，默认 20、最大 100。创建成员以 `username` 定位用户，不暴露用户邮箱；不存在的用户名返回 400。

## 8. 权限与资源一致性

- 所有知识库、模型配置和应用查询首先解析当前工作空间，再以 `workspace` 过滤。
- Serializer 接收已解析的 `workspace`，名称冲突、模型绑定、知识库绑定均在同一空间校验。
- URL 中的资源 ID 即使真实存在，只要不属于当前工作空间即返回 404。
- 创建时同时写入 `workspace` 和 `owner=request.user`；后者仅表示创建者。
- 删除工作空间前检查根资源、成员与默认空间约束；不级联删除业务数据。
- 删除组织成员时同步删除该组织下对应工作空间的显式成员关系；最后 OWNER 规则优先。

## 9. 异步任务与公开访问边界

- 上传、重新处理、重试、取消在入队前要求 `document.process`；任务消息只携带任务主键。
- Worker 重新读取任务及文档归属，不接受 `workspace_id` 参数，因此无法通过伪造消息字段跨空间处理文档。
- 任务状态查询继续按 `Task → Document → KnowledgeBase.workspace` 过滤。
- 发布版本快照不复制工作空间密钥；运行时由 `Application.workspace` 和既有发布关系决定资源。
- 公开网页、访客会话和 OpenAI 兼容 API 不读取浏览器工作空间上下文，也不因内部工作空间切换改变已发布版本。

## 10. 审计策略

记录组织/工作空间/成员变更、角色调整、知识库与模型配置增删改、模型测试、文档删除/重处理/重试/取消、应用发布/回滚/停用、凭证与公开访问变更，以及权限拒绝。

审计写入使用独立 Service，并尽量通过 `transaction.on_commit` 在业务事务成功后写 SUCCESS；拒绝事件直接记录 REJECTED。审计写入失败不得破坏主要业务，但必须写安全日志。审计查询必须具备 `audit.read` 且只能查询当前组织；有工作空间上下文时默认限制当前空间。

## 11. 页面交互

- 左侧导航顶部使用工作空间选择器，展示组织/工作空间和当前有效角色。
- 切换工作空间后清空页面级业务状态与失效 URL，跳转知识空间列表并重新加载。
- 增加“组织治理”页面，包含成员、工作空间、工作空间成员和审计四个区域。
- 前端根据 capabilities 隐藏或禁用新增、编辑、删除、处理、发布、密钥操作；后端仍是最终授权方。
- 组织/工作空间被禁用、成员被移除后，下一次请求会返回 404，前端刷新 `/me/context/` 并切换到仍可访问空间或退出业务页。

## 12. 安全威胁模型

| 威胁 | 控制 |
| --- | --- |
| 修改 Header/URL 枚举其他租户 | 工作空间成员验证 + 根资源二次 workspace 过滤 + 404 |
| 低权限用户伪造写请求 | 集中能力映射，View 只调用 `require_capability` |
| 修改角色移除最后 OWNER | 原子事务 + `select_for_update` + 409 |
| 应用绑定跨空间知识库/模型 | Serializer 按当前 workspace 校验 |
| 异步任务伪造 workspace | 队列仅传 task ID，Worker 沿数据库外键重新解析 |
| 审计中泄漏 API Key/Token/文档 | metadata 白名单、敏感键拒绝、值长度限制、IP HMAC |
| 审计被 API 篡改 | 仅 GET 路由，无写 Serializer；数据库应用账号仍需最小权限 |
| 浏览器切换空间后旧响应覆盖 | 页面卸载/重载和请求上下文 Header；后续可增加 AbortController |
| 公开接口被强制套用内部上下文 | public/v1 独立认证链，不调用 workspace Header 解析器 |

## 13. 测试矩阵

- 数据迁移：历史用户获得默认组织/空间，三类根资源归属正确，原子级联关系数量不变。
- 上下文：单空间兼容、多空间缺 Header、非法/越权/禁用空间。
- 租户隔离：知识库、文档、任务、会话、模型、应用跨空间均 404。
- 角色：五种工作空间角色与组织隐式角色逐项验证读、写、处理、发布、成员和审计能力。
- 管理：新增成员、角色更新、移除、最后 OWNER、防止跨组织管理、删除被引用空间 409。
- 审计：成功和拒绝事件、过滤分页、不可通过 API 修改、metadata 不含敏感值。
- 异步：任务从文档反查空间，错误空间不能入队或查询。
- 回归：登录、CRUD、上传、检索、SSE、Agent、模型配置、应用发布、公开网页与 OpenAI 兼容 API。
- 前端：类型检查、构建、切换空间、权限按钮、组织治理空/错/加载态。

## 14. 设计自审

- 选择 capability 而非在 View 散落角色字符串，便于角色演进且降低漏判。
- 不把所有子表都增加 workspace，避免重复数据和迁移风险；根资源边界足以沿外键证明归属。
- 保留 owner 但取消其授权意义，兼容历史展示和创建者追踪；后续版本可在完整迁移验证后再决定移除。
- 不允许多空间用户无 Header 自动取默认值，避免后台标签页或过期 localStorage 静默访问错误租户。
- 不物理删除组织，审计保留策略优先；工作空间删除仅允许空空间。
- 审计不是运行日志的替代，应用访问日志仍保留延迟与请求指标。

## 15. 实施与验证记录

本节在每个垂直切片完成后更新，记录迁移文件、接口、测试数量、HTTP 与浏览器证据。当前仅记录开发前基线，尚未宣称阶段完成。

## 16. 已知限制

- SQLite 可验证事务逻辑，但 `select_for_update` 的并发互斥能力需在 PostgreSQL 生产环境复验。
- 本阶段无邀请邮件，新增成员要求账号已存在。
- 不提供组织物理删除、审计导出、自定义角色和资源级 ACL。
- 前端生产构建在当前受限执行环境中存在 `esbuild spawn EPERM` 基线限制，需在普通本机终端补验。
