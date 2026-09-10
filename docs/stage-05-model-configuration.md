# 第五阶段设计：模型配置管理

## 1. 阶段目标

在保留知识库、文档安全重处理、RAG、SSE、引用和会话历史的基础上，增加用户级 OpenAI 兼容模型配置。Chat 与 Embedding 独立配置，知识库可分别选择；API Key 使用独立主密钥加密，模型地址做 SSRF 防护，Embedding 配置变化通过版本签名阻止旧向量被静默混用。

## 2. 现状审计

### 2.1 当前能力

- `settings.load_local_env()` 读取 `backend/.env` 并通过 `os.environ.setdefault()` 注入四个全局模型变量。
- `rag.stream_answer()` 只有在 `OPENAI_API_KEY + LLM_MODEL` 同时存在时才调用 OpenAI Chat Completions，否则返回固定演示回答。
- `embeddings.embed_texts()` 只有在 `OPENAI_API_KEY + EMBEDDING_MODEL` 同时存在时才调用外部 Embedding，否则使用 256 维本地哈希向量。
- Chat 与 Embedding 共用 `OPENAI_BASE_URL / OPENAI_API_KEY`，不能选择不同服务商。
- `KnowledgeBase` 没有模型关联；`Document / Paragraph` 没有记录向量来源。
- `cosine_similarity()` 对向量维度不一致只返回 0，会掩盖错误配置。
- SDK 超时未显式设置，原始异常可能进入 HTTP/SSE、文档错误字段和日志。
- 页面底部固定显示“本地演示模式”，即使真实大模型已经启用也会误导用户。
- `.gitignore` 已排除 `backend/.env`；现有 Serializer 没有密钥字段。

### 2.2 安全与测试缺口

- 用户一旦能填写 Base URL，后端将成为潜在 SSRF 跳板。
- 如果把 API Key 直接加入普通 ModelSerializer，容易在列表、详情或错误中泄漏。
- 当前测试会继承真实 `.env`；已经配置 Chat 模型时，原流式测试可能访问真实网络并产生费用。
- Embedding 配置改变后，旧 Paragraph 仍会被无条件检索。

### 2.3 基线（2026-08-23）

- `py -3.10 manage.py test`：在测试进程临时清空真实模型环境变量后，23 项全部通过；这项临时处理证明现有功能正常，同时暴露出测试未主动隔离外部模型的问题。
- `showmigrations`：`api.0001_initial` 已应用。
- `makemigrations --check --dry-run`：无变更。
- `npm run type-check`：通过。
- `npm run build`：通过；保留第三方 PURE 注释和主包约 1.13 MB 的既有警告。
- `MODEL_CONFIG_ENCRYPTION_KEY` 尚未配置，`cryptography` 尚未安装。

## 3. 范围与非范围

### 包含

1. 用户级 Chat/Embedding 配置 CRUD 和分页。
2. 独立密钥、地址、模型名、超时、连接测试。
3. Fernet 加密、脱敏输出和密钥轮换。
4. SSRF 地址校验和安全错误映射。
5. 知识库分别选择 Chat 与 Embedding。
6. 环境变量兼容回退和生效状态展示。
7. Embedding revision/signature、失效提示和安全重处理。
8. 配置页面、知识库轻量设置区和完整权限/回归测试。

### 不包含

Redis、Celery、WebSocket、pgvector、OCR、对象存储、RBAC、Agent、计费、Token 统计、模型市场、自动路由、多模型并行、导出和大规模 UI 重构。

## 4. 安全威胁模型

| 威胁 | 入口 | 控制 |
|---|---|---|
| 数据库泄漏导致 API Key 泄漏 | ModelConfig 持久化 | Fernet 密文；主密钥只来自环境变量 |
| 列表/详情泄漏明文或密文 | Serializer | 显式白名单字段；只返回 configured/masked |
| PATCH 把掩码保存为密钥 | 编辑表单/API | 缺省或空字符串保留；拒绝掩码形态 |
| 日志/异常泄密 | SDK 异常 | 白名单错误映射；不返回 `str(exc)` |
| 跨用户配置访问 | URL/请求体伪造 ID | owner 过滤；知识库与配置 owner 双校验 |
| SSRF 访问内网/元数据 | 用户 Base URL | URL 语法、DNS 与 IP 分类校验；生产禁私网 |
| DNS 重绑定 | 保存时域名安全、调用时变更 | 保存和每次调用前都解析校验 |
| 重定向绕过地址限制 | 上游 3xx | OpenAI 使用不跟随重定向的 httpx Client |
| 向量模型切换后混用旧向量 | 知识库选择/配置编辑 | revision + Document signature；检索只用匹配数据 |
| 自动化测试访问真实服务 | 测试继承 `.env` | 测试类清空环境、Mock OpenAI、禁止真实网络 |

## 5. 数据模型与 Migration

### 5.1 ModelConfig

- `owner`: User CASCADE。
- `name`: 100 字符；`(owner, name)` 唯一。
- `model_type`: `CHAT / EMBEDDING`，创建后只读。
- `base_url`: 规范化后的根地址。
- `model_name`: 200 字符。
- `encrypted_api_key`: Fernet 密文；永不序列化。
- `api_key_last4`: 仅供掩码展示。
- `timeout_seconds`: 1—120，默认 30。
- `revision`: 默认 1。
- `last_test_status`: `UNTESTED / SUCCESS / FAILURE`。
- `last_test_message`: 只保存安全短消息。
- `last_test_at`: 可空。
- `embedding_dimension`: Embedding 测试成功后记录，可空。
- `created_at / updated_at`。

仅修改 `base_url / model_name` 时 revision 加 1；名称、超时、API Key 轮换不改变向量语义，故不增加 revision。API Key 可能改变权限，但不应让所有文档无条件失效。

### 5.2 KnowledgeBase

- `chat_model_config`: nullable，`RESTRICT`。
- `embedding_model_config`: nullable，`RESTRICT`。

使用 `RESTRICT` 的原因：直接删除正在使用的配置会被阻止；删除整个用户时，知识库和配置都在同一个级联收集集合中，仍可正常清理。API 将约束异常转换为 409。

### 5.3 Document

- 新增 `embedding_signature`，默认空字符串。
- 旧数据保持空签名，代表 legacy。
- 显式 Embedding 配置的签名为 `config:{id}:revision:{revision}`。
- 未显式选择配置时维持旧逻辑，不用签名过滤，以兼容已有环境变量/本地向量。

Migration 只新增表和 nullable/default 字段，不搬运 `.env` 密钥、不修改现有 Paragraph、不删除数据。

## 6. 密钥加密与轮换

- 使用 `cryptography.fernet.Fernet`。
- 主密钥来自 `MODEL_CONFIG_ENCRYPTION_KEY`，不回退到 `DJANGO_SECRET_KEY`。
- 项目可以在主密钥缺失时启动并继续使用 legacy 环境变量模式；创建、轮换、解密数据库密钥时返回“服务器尚未配置模型密钥加密能力”。
- 创建配置必须提供非空 API Key；本阶段以远程 OpenAI 兼容服务为目标，不把无密钥本地端点扩进产品契约。
- PATCH 未包含 `api_key` 或传空字符串表示保留；非空表示加密轮换；`••••`/`****` 形态拒绝。
- 列表使用 `api_key_last4` 生成 `••••••••abcd`，不批量解密。
- 主密钥丢失或更换后旧密文不可恢复；运维必须把主密钥与数据库备份配套保存。

## 7. Base URL 与 SSRF 防护

- 只接受 `http/https`，禁止凭据、query、fragment，host 必须存在；去除末尾 `/`。
- `https` 默认允许；`http` 只在 `DEBUG=true` 且 `ALLOW_PRIVATE_MODEL_ENDPOINTS=true` 时允许。
- 解析所有 A/AAAA 地址并拒绝 loopback、private、link-local、multicast、reserved、unspecified；元数据地址自然落入 link-local。
- `localhost` 和 `.localhost` 直接拒绝。
- 保存和每次连接/模型调用前都校验；解析失败时失败关闭。
- 开发时仅当 `DEBUG && ALLOW_PRIVATE_MODEL_ENDPOINTS` 才允许本地 Ollama/Mock。
- OpenAI client 注入 `httpx.Client(follow_redirects=False)`。

## 8. Service 职责

- `model_crypto.py`：加密、解密、掩码；不接触 HTTP。
- `model_endpoint_security.py`：规范化、DNS/IP 安全校验。
- `model_clients.py`：统一配置对象、客户端创建、超时和安全错误类型/映射。
- `model_connectivity.py`：Chat/Embedding 最小测试，记录耗时、状态、维度；不创建会话。
- `model_resolution.py`：数据库配置优先，环境变量其次，本地回退最后；生成 Embedding signature 和前端安全状态。
- `rag.py / embeddings.py / document_processor.py`：接收 KnowledgeBase 并通过 resolution/client service 工作，不再各自重复读取全局变量。

## 9. API 契约

### 9.1 配置列表与创建

```http
GET /api/model-configs/?model_type=CHAT&page=1&page_size=20
POST /api/model-configs/
```

列表分页，默认 20、最大 100，只查询 `owner=request.user`。创建接收：

```json
{
  "name": "DeepSeek Chat",
  "model_type": "CHAT",
  "base_url": "https://api.deepseek.com",
  "api_key": "secret",
  "model_name": "deepseek-v4-pro",
  "timeout_seconds": 30
}
```

### 9.2 详情、修改、删除、测试

```http
GET/PATCH/DELETE /api/model-configs/{config_id}/
POST /api/model-configs/{config_id}/test/
```

- 不存在或越权统一 404。
- model_type 不可修改。
- 被知识库引用时 DELETE 返回 409。
- test 成功返回类型、耗时和 Embedding 维度；失败返回安全消息，并更新安全测试状态。

### 9.3 知识库模型选择

```http
GET/PATCH /api/knowledge-bases/{knowledge_id}/model-config/
```

- PATCH 字段缺省表示保持，`null` 表示回退系统默认。
- Chat 位置只接受当前用户 CHAT 配置；Embedding 同理。
- 跨用户配置按不存在处理。
- 响应包含选择 ID、安全展示信息、生效来源和需要重处理的文档数。

所有响应继续使用 `{code,message,data}`，永不包含密文、明文密钥、主密钥、请求头或 SDK 对象。

## 10. Serializer 划分

- `ModelConfigReadSerializer`：列表/详情白名单字段、掩码、configured；不解密。
- `ModelConfigWriteSerializer`：创建/PATCH、类型不可变、URL与超时校验、加密轮换、revision。
- `KnowledgeBaseModelSelectionSerializer`：owner、类型和 null/缺省语义。
- `KnowledgeBaseModelStatusSerializer` 由 Service 组织安全状态，不暴露环境变量值或密钥。
- `DocumentSerializer` 新增只读 `needs_reprocess`，不返回 signature。

## 11. Chat/Embedding 解析优先级

```text
知识库显式数据库配置
→ 原环境变量配置
→ Chat 演示回答 / 本地哈希 Embedding
```

`.env` 中的真实密钥不导入数据库，也没有读取它的 API。Chat 与 Embedding 分别解析，允许使用不同数据库配置。

## 12. Embedding signature 与安全替换

- 文档处理开始时计算目标 signature，但旧 Paragraph/signature 保持。
- 外部向量全部准备成功后，在同一事务删除旧 Paragraph、写新 Paragraph、写目标 signature。
- 失败时恢复旧 Paragraph 和旧 signature；如果显式配置已变化，它仍表现为 `needs_reprocess=true`，不会参与新配置检索。
- 显式配置下，搜索 QuerySet 只选择 signature 匹配且 SUCCESS 的文档。
- 无匹配文档时返回空引用，而不是把不同维度相似度当作 0 混入排序。
- 配置 base/model 编辑使 revision 增长，签名自然失配；Chat 配置编辑不影响文档。

## 13. 连通性、超时与错误映射

Chat 使用非流式、短提示、短输出，不创建 Conversation/Message；Embedding 使用固定无敏感文本并验证非空数值向量，记录维度。

安全映射：

| 情况 | 用户消息 |
|---|---|
| 401/403 | API Key 无效或没有模型权限 |
| 404 | 模型地址或模型名称不存在 |
| 408/SDK timeout | 模型服务响应超时 |
| 429 | 请求频率过高、额度不足或服务限流 |
| 连接/DNS | 无法连接模型服务，请检查地址和网络 |
| 非法响应 | 服务响应不符合 OpenAI 兼容格式 |
| 加解密 | 服务器模型密钥配置异常 |

原始异常不进入 HTTP/SSE、Document.error_message 或 last_test_message。

## 14. 页面交互

- 新增 `/model-configs` 与顶栏“模型配置”入口。
- 页面以 Chat/Embedding 标签筛选，包含分页列表、新建/编辑弹窗、password 密钥框、连接测试和删除。
- 编辑不回填密钥；空值保留；提交后立即清空响应式变量。
- 知识库详情页增加轻量“模型设置”抽屉，分别选择 Chat/Embedding，展示生效来源和失效文档数。
- 固定演示文案替换为后端返回的实际安全状态。
- Embedding 切换后只提示并提供逐文档重新处理入口，不在同步请求里批量重处理。

模型配置页状态只属于该路由，采用页面状态；认证仍由 Pinia 管理。知识库状态留在详情页，避免无意义的全局 Store。

## 15. 权限与删除

- 配置 CRUD/test：`id + owner`。
- 知识库模型选择：`knowledge_base.id + owner`，配置再验证 `id + owner + model_type`。
- 绑定他人配置、错误类型或伪造 ID 不产生任何修改。
- 正在被知识库引用的配置删除返回 409；用户先从知识库回退默认后才能删除。
- 删除知识库不会删除可复用的 ModelConfig。

## 16. 测试矩阵

覆盖附件要求的 35 项，并额外覆盖：

- 主密钥缺失时不明文降级。
- 错误主密钥不能泄漏密文或原异常。
- 掩码不能作为 PATCH 新密钥。
- revision 只在向量语义字段改变时递增。
- 地址保存与调用前均校验。
- SSE 错误使用安全映射且不创建空 AI 消息。
- 测试运行时显式移除真实模型环境变量，OpenAI 调用全部 Mock。

## 17. 垂直切片

1. 加密、SSRF、ModelConfig、Migration、只读 Serializer 和安全单测。
2. CRUD、owner、删除冲突、前端配置页。
3. 连接测试、错误映射、前端测试状态。
4. KnowledgeBase Chat 选择、resolution、RAG/SSE 回归。
5. Embedding 选择、Document signature、文档处理/检索和失效 UI。

每个切片运行相关后端测试和前端类型检查。

## 18. 设计自审

- **密钥：** 明文只作为写入输入和短暂解密结果；列表不解密。没有主密钥时失败关闭。
- **SSRF：** 语法与解析地址双校验，调用前复验，不跟随重定向；开发私网是显式双开关。
- **权限：** 所有 ID 都由当前用户 QuerySet 解析，不信任前端类型标签。
- **Embedding 一致性：** revision/signature 派生失效，不删除旧数据，不依赖批量异步任务。
- **兼容性：** nullable 选择保留 `.env` 与本地模式；旧文档在 legacy 模式继续工作。
- **删除：** RESTRICT 防止悬空选择，知识库删除不误删配置。
- **测试成本：** 自动测试禁真实网络；浏览器用临时 Mock 或经授权的无敏感短调用。
- **复杂度：** 只拆出安全边界明确的 Service，不引入 Repository/Manager 体系。

## 19. 实际实施与验证记录

### 19.1 已实施

- 新增 `ModelConfig`、KnowledgeBase 两个模型外键和 `Document.embedding_signature`，生成并应用 `api.0002` additive migration。
- 新增 Fernet 加解密/脱敏、模型地址安全、统一客户端、连通性、配置解析五个 Service；`rag / embeddings / document_processor` 已改为复用这些边界。
- 新增配置 CRUD、连接测试、知识库模型选择 API；所有资源通过 owner QuerySet 隔离，校验失败保持统一响应外壳。
- 新增 `/model-configs` 页面、知识库列表入口、知识库模型设置抽屉、动态生效状态、失效文档标记。
- API Key 编辑不回填、不持久化在浏览器、保存后立即清空；列表和详情只返回掩码。
- 原测试进程现在主动清空模型环境变量；所有连通性调用使用 Mock，不访问真实外部服务。

### 19.2 自动化证据（2026-08-23）

- `py -3.10 manage.py test`：32 项通过（原 23 项回归 + 9 组 Stage 05 安全/功能测试）。
- `py -3.10 manage.py makemigrations --check --dry-run`：`No changes detected`。
- `py -3.10 manage.py check`：无系统检查问题。
- `npm run type-check`：通过。
- `npm run build`：通过；产物主 JS 约 1.15 MB。保留第三方 PURE 注释位置和 chunk 大于 500 kB 两类无关警告。

### 19.3 真实运行与浏览器验收（2026-08-23）

使用本机 `127.0.0.1:18080` 临时 OpenAI 兼容 Mock、临时用户、假密钥和无敏感测试文档完成真实前后端点击验收：

1. 登录、进入模型配置页，分别创建 Chat/Embedding 配置。
2. 保存后页面只显示 `••••••••last4`；刷新和编辑均不回填明文。
3. Chat 连接测试成功；Embedding 成功并显示 3 维。
4. 创建临时知识库，分别选择 Chat/Embedding，页面即时显示真实生效来源。
5. 上传测试文档后由显式 Embedding 处理，状态成功、切片数为 1。
6. 提问后收到 Mock SSE 完整回答、引用；URL 写入 conversation，刷新后消息与引用恢复。
7. 编辑 Embedding 模型名令 revision 从 1 增长，文档出现“向量需更新”，页面阻止发送，未混用旧向量。
8. 重新处理后警告消失；再次提问恢复检索、SSE 与引用。
9. 错误密钥显示“API Key无效或没有模型权限”；错误模型显示“不存在”；1 秒配置显示“模型服务响应超时”。
10. 前端控制台无 warning/error；后端访问日志没有 API Key 或请求体。

验收后已精确删除 `stage05-browser-user`，其知识库、文档、会话、消息和模型配置均级联清理；验证物理上传文件不存在，并删除临时 Mock/测试文本。未调用真实外部模型，也未读取或输出真实 API Key。

## 20. 已知限制

- 文档重新处理仍为同步操作，没有跨进程锁和批量后台任务。
- 模型配置仅支持 OpenAI Chat Completions 与 Embeddings 兼容接口。
- 不实现代理、企业证书、模型计费、用量统计或自动路由。
- 主密钥轮换工具不在本阶段自动化；轮换前必须先解密并重新加密现有配置。
