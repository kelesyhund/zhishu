# 第四阶段设计：对话历史与会话管理

## 1. 阶段目标

在保留登录、知识库 CRUD、文档管理、RAG 检索、SSE 流式回答和引用展示的基础上，新增可持续使用的会话历史：第一次提问创建会话，历史列表可分页查看，消息可恢复，会话可改名和删除，当前会话通过 URL 恢复。

## 2. 现状审计

### 已有能力

- `Conversation` 已关联 `KnowledgeBase` 和 `owner`，包含 `title / created_at`。
- `Message` 已关联 `Conversation`，包含 `role / content / references / created_at`，删除会话时数据库会级联删除消息。
- `ChatStreamView` 在请求开始时保存用户消息，在流式成功结束后保存 AI 消息和公开引用。
- 前端能接收 SSE 的 `meta / content / references / done / error`，但 `conversationId` 只在组件内存中保存。

### 基线验证（2026-08-22）

- `py -3.10 manage.py test`：12 个测试通过。
- `npm run type-check`：通过。
- `npm run build`：通过；保留第三方 PURE 注释和单包大于 500 kB 的既有警告。
- `showmigrations api`：`0001_initial` 已应用。
- `makemigrations --check --dry-run`：无待生成迁移。

### 发现的问题

- `ConversationListView` 使用嵌套 `ConversationSerializer`，列表中每条会话都会返回全部消息，数据量随历史增长而放大，并存在预取不足导致的 N+1 风险。
- 显式传入不存在、跨知识库或其他用户的 `conversation_id` 时，当前代码会静默创建新会话，而不是拒绝越权目标。
- 没有会话详情、改名、删除和独立消息分页接口。
- 会话只按创建时间排序，新消息不会使旧会话回到顶部。
- 前端刷新后丢失当前会话，也不能恢复历史消息。
- 流式生成返回空内容时仍可能保存空 AI 消息；部分输出后异常时缺少明确的数据保留契约。

## 3. 范围与非范围

### 本阶段包含

1. 会话列表、详情、改名和删除。
2. 历史消息分页和“加载更早消息”。
3. 第一次提问创建会话并生成默认标题。
4. 用户消息、完整 AI 消息和引用持久化。
5. URL 查询参数恢复当前会话。
6. 会话抽屉、新建、切换、改名和删除交互。
7. 嵌套 owner 权限、级联删除、流式失败和回归测试。

### 本阶段不包含

Redis、Celery、WebSocket、pgvector、OCR、对象存储、RBAC、Agent、模型配置、多会话共享、导出、大规模 UI 重构和无关依赖升级。

## 4. 数据模型与 Migration

本阶段不增加数据库字段，也不生成迁移：

- `message_count` 使用 `Count("messages")` 查询注解得到。
- `last_message_at` 使用 `Max("messages__created_at")` 查询注解得到；当前产品不创建空会话，因此每条会话都有消息。
- 会话列表按 `-last_message_at, -id` 排序，新消息会使会话回到顶部。
- 默认标题只在第一次提问创建会话时写入；改名后的已有会话不会再次进入自动命名流程，因此不需要 `is_title_custom` 字段。

这避免把可计算的展示字段重复保存到数据库，也避免现有数据迁移。实施后再次运行迁移检查。

## 5. API 契约

所有接口使用统一响应外壳，并按 `knowledge_base.owner + knowledge_base_id + conversation.id + conversation.owner` 查询。不存在、跨用户或知识库不匹配统一返回 HTTP 404。

### 5.1 会话列表

```http
GET /api/knowledge-bases/{knowledge_id}/conversations/?page=1&page_size=20
```

- 默认 20，最大 100。
- 按最后消息时间倒序。
- 返回 `items / total / page / page_size / total_pages`。
- item 只包含 `id / title / message_count / created_at / last_message_at`，不嵌套消息。

### 5.2 会话详情、改名和删除

```http
GET /api/knowledge-bases/{knowledge_id}/conversations/{conversation_id}/
PATCH /api/knowledge-bases/{knowledge_id}/conversations/{conversation_id}/
DELETE /api/knowledge-bases/{knowledge_id}/conversations/{conversation_id}/
```

- PATCH 只接收 `title`，去除首尾空格，空标题返回 400，最大 100 字符沿用模型校验。
- DELETE 成功返回 `data: true`，关联 Message 由外键级联删除。

### 5.3 历史消息

```http
GET /api/knowledge-bases/{knowledge_id}/conversations/{conversation_id}/messages/?page=last&page_size=50
```

- 默认 50，最大 100。
- 数据按 `created_at, id` 从旧到新。
- `page=last` 用于首次打开时加载最新一页；前端根据 `previous_page` 向前加载。
- 返回 `items / total / page / page_size / total_pages / has_previous / previous_page`。

### 5.4 流式问答

```http
POST /api/knowledge-bases/{knowledge_id}/chat/stream/
```

- 未提供 `conversation_id`：由第一条问题创建会话，标题为压缩空白后的问题前 50 个字符。
- 显式提供 `conversation_id`：必须命中当前用户和当前知识库；否则返回 404，绝不静默新建。
- 非数字或非正数 ID 视为不存在并返回 404。

## 6. Serializer 职责

- `ConversationListSerializer`：服务列表卡片，只接受查询注解的数量和最后消息时间，不加载 `messages`。
- `ConversationDetailSerializer`：服务详情和更新后的返回值，只返回会话元数据。
- `ConversationTitleSerializer`：只负责改名输入校验，避免把只读统计字段变成可写字段。
- `MessageSerializer`：服务历史恢复，返回 `id / role / content / references / created_at`。

列表与消息分离后，请求体积由“所有会话的全部消息”降为“当前页会话摘要”，也消除了逐会话读取 messages 的 N+1 路径。

## 7. 页面交互与前端状态

- 聊天页头增加“历史会话”按钮，打开右侧抽屉；不改变现有文档侧栏和问答主体。
- 抽屉包含新建对话、分页会话列表、当前项、消息数量/活跃时间、改名和删除。
- 状态保留在 `KnowledgeDetailView`：这些状态只服务当前路由页面；Pinia 继续只管理跨页面认证，避免为了使用 Pinia 强行扩大共享状态。
- 当前会话写入 `?conversation={id}`。刷新时先校验并加载该会话；404 时清除参数并进入新对话。
- 切换会话使用递增请求序号，过期响应不得覆盖后选择的会话。
- 发送期间禁止新建、切换、改名和删除，避免流式内容写入错误的前端会话。
- 删除当前会话后统一进入“新对话”状态并清除 URL；删除非当前会话不影响当前消息。
- 流式完成后刷新会话第一页，使当前会话移动到顶部。

## 8. 会话与流式状态变化

```text
新对话前端状态
→ 第一次发送
→ 后端创建Conversation和USER Message
→ SSE meta返回conversation_id
→ 前端写入URL
→ SSE持续输出内容
→ 成功完成后保存ASSISTANT Message和references
→ 会话出现在历史列表顶部
```

已有会话只追加消息，不再自动修改标题。

## 9. 流式失败和中断策略

- 用户消息在创建流式响应前保存，代表服务器已经接收了问题。
- AI 消息只在生成正常结束且内容非空时保存。
- 生成器在输出前、输出中途报错或客户端中断时，都不保存空消息或残缺 AI 消息；已保存的用户消息保留，便于用户看见问题并重试。
- SSE `error` 返回可理解的错误；前端临时展示错误，但刷新历史后只恢复真实持久化数据。
- 搜索阶段如果在流式响应创建前失败，使用统一 400 错误，不产生 AI 消息。

## 10. URL 恢复策略

- 打开页面读取 `route.query.conversation`。
- 仅接受正整数；无效格式直接移除。
- 请求会话详情和最后一页消息；任一资源 404 时清除参数和本地消息。
- 成功切换或收到新会话的 `meta` 后使用 `router.replace()` 写入 ID，避免每条消息制造浏览器历史记录。

## 11. 权限、删除与级联

- 所有会话辅助查询同时限定 `id / knowledge_base_id / knowledge_base__owner / owner`。
- 显式伪造 conversation ID 的聊天请求返回 404。
- `Conversation → Message` 和 `KnowledgeBase → Conversation` 已使用 `CASCADE`，不需要 Signal；两者都只涉及数据库记录，没有类似 FileField 的物理文件。

## 12. 垂直切片

1. 后端会话摘要、详情、改名、删除和权限测试。
2. Message 分页、前端抽屉、切换与 URL 恢复。
3. 新建、改名、删除和页面状态同步。
4. 流式权限、标题、成功持久化和失败保留规则。

每个切片完成后运行相关后端测试和前端类型检查。

## 13. 测试矩阵

| 场景 | 预期 |
|---|---|
| 自己的会话列表 | 分页、最新活跃优先、不嵌套 messages |
| 他人/错误知识库会话 | 所有资源接口 404 |
| 会话详情和改名 | 正常返回；标题 trim；空标题 400 |
| 消息分页 | 确定顺序，`page=last` 和上一页元数据正确 |
| 删除会话 | Conversation 与 Message 均消失 |
| 删除知识库 | Conversation 与 Message 级联消失 |
| 无 ID 第一次提问 | 创建会话，标题正确 |
| 有效 ID 后续提问 | 追加消息，不覆盖手工标题 |
| 伪造 ID | 404，不创建会话和消息 |
| 流式成功 | USER、ASSISTANT、references 全部保存 |
| 流式失败 | USER 保留，ASSISTANT 不创建 |
| 原有功能 | 现有 12 项测试继续通过 |

## 14. 设计自审

- **权限：** 不复用只按主键查找的宽松路径；统一嵌套 owner 查询。
- **性能：** 列表使用 `Count/Max` 一次聚合，不读取消息正文；消息最大每页 100。
- **一致性：** 不预创建空会话；AI 消息只在完整成功后落库。
- **前端竞态：** 发送时锁定切换操作；历史请求使用序号丢弃过期结果。
- **兼容性：** 保留现有 SSE 事件和聊天 URL；只是收紧伪造 conversation ID 的行为。
- **复杂度：** 使用现有 APIView、Paginator 和页面状态，不新增全局会话 Store、Repository 或 WebSocket。
- **数据安全：** 自动化和浏览器只创建、删除 `stage04-*` 临时数据。

## 15. 实施与验证记录

### 实际实施

- 会话列表改为 `Count/Max` 聚合摘要和服务端分页，不再嵌套全部消息。
- 新增会话详情、标题修改、删除和独立消息分页接口，所有资源使用知识库、会话和 owner 联合查询。
- 新增 `services/conversations.py`，统一负责默认标题、用户问题落库、非空 AI 消息落库和公开引用裁剪。
- 显式伪造、跨用户、跨知识库和非法 `conversation_id` 均返回 404，不再静默创建会话。
- 前端新增历史会话抽屉、新建状态、改名、删除、会话分页、消息恢复和“加载更早消息”。
- 当前会话通过 URL 查询参数保存；无效或已删除会话会被清除并进入新对话。
- 历史请求使用递增序号防止过期响应覆盖，发送期间禁止切换和破坏性会话操作。
- 流式或检索失败只保留用户消息；空回答、部分回答和异常回答都不保存 AI Message。
- README 和 TODO 已同步第四阶段能力。

### 自动化验证

- 基线：后端 12 项测试、前端类型检查和生产构建通过。
- 实施后：`py -3.10 manage.py test` 共 23 项测试通过。
- 覆盖摘要分页、最后活跃排序、详情、改名、标题校验、嵌套权限、消息分页、两种级联删除、首问建会话、后续消息、伪造 ID、完整引用、部分失败、空回答和检索失败。
- `npm run type-check` 通过；`src` 中无 JavaScript 副本、`any` 或 TypeScript 忽略指令。
- `npm run build` 通过；保留第三方 PURE 注释和主包大于 500 kB 的既有非阻断警告。
- `makemigrations --check --dry-run` 无变化，确认本阶段不需要 Migration。

### 真实 HTTP 验证

使用 `stage04-smoke-*` 临时知识库完成并清理：登录、上传测试文档、首问创建会话、会话摘要分页、历史消息恢复、标题修改、二次提问、标题保持、消息数由 2 更新为 4、删除会话和知识库。列表响应确认没有 `messages` 嵌套字段。

### 浏览器验收

- 使用已登录的本地 `demo` 会话进入 `stage04-browser-*` 临时知识库。
- 验证空历史抽屉和“新建对话”；首问“什么是 RAG？”后自动生成标题并写入 `?conversation=4`。
- 历史列表显示 2 条消息和当前会话；改名为 `stage04-浏览器改名` 后列表和聊天页头同步更新。
- 发送第二个问题后列表更新为 4 条消息，手工标题保持不变。
- 刷新页面后 URL、4 条历史消息和两组引用全部恢复。
- 创建第二个独立会话并在两条会话间来回切换；聊天区域交叉消息计数均为 0。
- 打开删除确认框，确认文案说明全部消息会被删除；浏览器中未点击最终确认。
- 通过真实 DELETE API 删除当前临时会话后刷新页面，验证无效 URL 自动清除、消息清空并回到“新对话”。
- 全部 `stage04-*` 临时知识库、文档、会话和消息已清理，数据库残留数量为 0。

浏览器自动化安全规则要求最终删除动作由用户确认，因此没有把“浏览器点击最终确认删除”标记为通过；删除接口、级联数据、前端删除处理分别由自动化测试、真实 HTTP 和删除后刷新恢复交叉覆盖。浏览器使用已有 `demo` 登录会话，真实登录接口由 HTTP 冒烟覆盖。

### 代码复查修正

- 修复检索在 SSE 建立前失败时前端拿不到新会话 ID 的问题：HTTP 错误数据中的 `conversation_id` 也进入 `meta` 状态处理。
- 修复历史请求与“新建对话”的 loading 竞态，并保留序号丢弃过期响应。
- 将包含嵌套操作按钮的会话卡片改为独立选择按钮和同级编辑/删除按钮，避免键盘事件误触发切换。

## 16. 已知限制

- SSE 仍为同步进程内生成；本阶段不做断线续传。
- 用户消息保存后若生成失败，需要用户主动重试。
- 普通页码分页在极高并发写入下可能发生页边界移动；当前单用户轻量项目可接受。
