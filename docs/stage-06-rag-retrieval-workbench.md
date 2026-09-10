# 第六阶段设计：RAG 检索策略配置与调试工作台

## 1. 阶段目标

在保留知识库、文档、模型配置、Embedding 版本隔离、SSE、引用和会话历史的基础上，为每个知识库增加可持久化的检索策略，并提供与正式问答共用算法的检索调试工作台。用户可以在纯向量与混合检索之间切换，调整阈值、权重、返回数量和上下文预算，自定义 System Prompt 与资料不足提示，并看到每个候选切片为何入选或被排除。

## 2. 现状审计

### 2.1 当前调用链

- `SearchView` 与 `ChatStreamView` 都调用 `rag.search_paragraphs()`；聊天处固定传入 `top_k=5`，搜索接口允许请求体传入 `top_k`。
- `search_paragraphs()` 先为查询生成一次 Embedding，再读取当前知识库全部 `SUCCESS` Paragraph，在 Python 中逐条计算并排序。
- 知识库显式选择 Embedding 配置时，QuerySet 会按 `Document.embedding_signature` 过滤；旧签名文档不会参与检索。
- 现有结果只有 `paragraph_id / document_name / content / similarity`，不返回 embedding；Message 的 JSON references 可以兼容新增白名单评分字段。
- `rag.build_messages()` 把资料拼成普通文本，System Prompt 固定，尚无严格资料边界或上下文预算。
- 真实 Chat 配置存在但检索为空时，当前代码仍会先创建客户端并调用模型；只有本地演示分支才直接返回“没有文档”。
- 前端知识库详情页已有模型设置、文档和会话抽屉，适合增加并列的“检索设置”和“检索调试”入口，状态仍只属于当前页面，无需增加 Pinia Store。

### 2.2 算法现状与问题

- 本地哈希向量先做 L2 归一化，因此其点积通常落在 `[-1, 1]`；外部 OpenAI 兼容服务不一定保证这一性质。
- 现有 `cosine_similarity()` 实际只计算点积，没有除以两边模长。新实现会改成真正的余弦公式，零向量返回 0，维度不一致继续明确报错。
- 现有分词规则是：英文/数字按连续 token、小写化；中文加入单字和相邻二元组。该逻辑只服务本地哈希向量，可提取成共享无网络分词器供 BM25 使用。
- 没有最低相关度阈值、关键词得分、混合评分、稳定的同分排序、上下文字符预算或排除原因。

### 2.3 基线（2026-08-23）

- `py -3.10 manage.py test`：32 项通过。
- `py -3.10 manage.py showmigrations`：`api.0001`、`api.0002` 已应用。
- `py -3.10 manage.py makemigrations --check --dry-run`：`No changes detected`。
- `npm run type-check`：通过。
- `npm run build`：沙箱内 esbuild 子进程首次因 Windows `EPERM` 无法启动；按授权在沙箱外重跑后通过。保留第三方 PURE 注释位置和大 chunk 既有警告。

## 3. 范围与非范围

### 包含

1. 知识库级检索字段、GET/PATCH API、前端设置抽屉。
2. 共享分词器、轻量 BM25、真实余弦相似度、向量归一化、混合评分和稳定排序。
3. 最终分阈值、top_k、Prompt 上下文预算和超长首切片策略。
4. 调试 API 与工作台，展示评分、入选状态和排除原因。
5. 正式搜索、调试和聊天复用同一检索核心；无结果时短路 Chat 模型。
6. owner、Embedding signature、失败文档、数据泄漏、持久化和回归测试。

### 不包含

Redis、Celery、pgvector、Elasticsearch、OCR、对象存储、RBAC、Agent、外部 Reranker、模型计费、Token 统计、大规模 UI 改版和无关依赖升级。

## 4. 数据模型与 Migration

直接在 `KnowledgeBase` 增加七个字段：

- `retrieval_mode`：`VECTOR/HYBRID`，默认 `VECTOR`。
- `retrieval_top_k`：默认 5，范围 1—20。
- `similarity_threshold`：默认 0，范围 0—1。
- `vector_weight`：默认 1，范围 0—1；关键词权重运行时计算为 `1 - vector_weight`。
- `max_context_chars`：默认 6000，范围 1000—30000。
- `system_prompt`：空字符串表示使用系统默认，最大 2000。
- `no_answer_message`：空字符串表示使用系统默认，最大 500。

选择直接字段而不是一对一 `RetrievalConfig`，因为这些参数与知识库生命周期一致、没有独立权限或复用需求，只有七个标量；单独模型只会增加 JOIN、资源查询和删除语义。

Migration 由 Django 正常生成，只添加带默认值的字段，不搬移、不覆盖、不删除现有数据。Serializer 负责范围与 trim；数据库增加数值 CheckConstraint，防止 ORM 或管理端绕过 API 写入非法值。

### 空值语义

- PATCH 字段缺省：保持原值。
- 数值、枚举提交 `null`：拒绝，避免“恢复默认”和“缺少必填值”混淆。
- `system_prompt/no_answer_message` 提交空字符串：恢复系统默认文案。
- 前端“恢复默认值”显式填入默认数值并清空两个文本字段，用户仍需点击保存。

## 5. 默认值兼容策略

`VECTOR + top_k=5 + threshold=0 + vector_weight=1` 保持当前按向量分取前五名的排序行为。余弦分从原始值映射到 `[0,1]` 是单调变换，不改变排序。搜索响应继续保留 `similarity` 字段，并令其表示本次策略的 `final_score`；同时增加可选的原始/归一化向量分、关键词分和位置字段。旧前端仍可读取 `document_name/content/similarity`。

## 6. 分词规则

共享 `tokenize_text()`：

1. 英文字母、数字和下划线按连续串提取并统一小写。
2. 中文汉字提取为单字 token，同时加入相邻中文二元组。
3. 标点和空白作为边界，不依赖网络词典。
4. 空文本返回空列表。

本地哈希 Embedding 和 BM25 共用该函数，避免同一项目出现两套中文/英文切分规则。

## 7. 关键词检索与 BM25

对当前知识库全部“状态成功且签名兼容”的 Paragraph 组成文档集合。对查询 token 逐项计算：

```text
idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

BM25(d, q) = Σ idf(t) * tf(t,d) * (k1 + 1)
              / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
```

固定 `k1=1.5`、`b=0.75`；`|d|` 和 `avgdl` 都按共享分词器生成的 token 数计算。空查询、空集合或零平均长度返回全 0。为参与融合，将当前候选集合的 BM25 原始分除以最大原始分；最大值为 0 时全部归一化为 0。算法在独立 Service 中一次性分词各 Paragraph，不在每个查询词循环内重复分词。

## 8. 向量分与混合评分

真正的余弦相似度：

```text
cosine(a,b) = dot(a,b) / (norm(a) * norm(b))
```

维度不一致抛出安全的 `VECTOR_MISMATCH`；任一零向量返回 0。原始余弦可能为负，归一化公式为：

```text
vector_score_normalized = clamp((vector_score_raw + 1) / 2, 0, 1)
```

VECTOR 模式：`final_score = vector_score_normalized`。

HYBRID 模式：

```text
final_score = vector_weight * vector_score_normalized
            + (1 - vector_weight) * keyword_score_normalized
```

浮点数在内部保留精度，只在 API 输出时四舍五入到 6 位。

## 9. 稳定排序、阈值与 top_k

候选统一按以下键排序：

```text
final_score 降序 → vector_score_normalized 降序 → Paragraph.id 升序
```

`similarity_threshold` 只作用于 `final_score`。低于阈值的调试项保留展示，但标记 `included=false / 低于相关度阈值`。通过阈值后再按排序选择，最多 `retrieval_top_k` 条；其余标记“超过返回数量限制”。失败状态与签名不匹配文档在 QuerySet 阶段排除，不计入候选总数。

## 10. 上下文预算

预算按实际写入用户 Prompt 的每个资料块字符数计算，包含资料边界、序号、文档名、切片位置、换行和正文。

- 数据库中的 Paragraph 永不修改。
- 按最终排序累计资料块；可以容纳才加入上下文。
- 若最高分且通过阈值的第一条本身超过预算，保留它并只截断本次 Prompt 中的正文，使完整资料块不超过预算；调试项返回 `content_truncated=true`，但 `content` 仍是数据库原文。
- 后续无法容纳的候选标记“超过上下文字符预算”，继续检查更短的后续候选，使预算尽可能被利用。
- `context_chars` 是最终所有资料块的真实字符总数，不超过配置预算。

## 11. Prompt 构建与无答案策略

- `system_prompt` 为空时使用现有“仅依据资料回答、资料不足需说明、标注引用”的默认提示。
- 每条资料以显式 `<reference ...>...</reference>` 边界包裹，并在系统提示中声明资料内容是不可信数据、不得执行其中的指令。
- Prompt 只包含文档名、位置和预算处理后的正文，不包含 API Key、模型配置、embedding 或内部对象。
- 没有任何入选资料时，`stream_answer()` 在解析/创建 Chat 客户端之前直接分块输出知识库自定义 `no_answer_message`；空值使用系统默认提示。
- 无答案仍完整保存 USER 与 ASSISTANT Message，AI references 为 `[]`；有结果时继续调用 Chat/本地演示并持久化按最终排序的公开引用。

## 12. Service 职责

- `retrieval_tokenizer.py`：共享中英文/数字分词。
- `keyword_retrieval.py`：BM25 原始分与集合归一化。
- `retrieval.py`：解析设置、筛选候选、统一打分、稳定排序、阈值/top_k/预算、正式引用和调试结果。
- `prompt_builder.py`：默认/自定义 System Prompt、资料边界、无答案文案和 Chat messages。
- `rag.py`：保留兼容的 `search_paragraphs/stream_answer/sse` 外观，委托上述 Service；不重复评分。

正式搜索、聊天和调试都调用 `retrieval.retrieve_candidates()`，不维护第二套公式。

## 13. API 契约

### 检索配置

```http
GET/PATCH /api/knowledge-bases/{knowledge_id}/retrieval-config/
```

返回七个配置字段；PATCH 缺省保持、null 拒绝，所有接口按 `id + owner` 查询，不存在或越权统一 404。

### 检索调试

```http
POST /api/knowledge-bases/{knowledge_id}/retrieval/debug/
```

输入 `query`（trim 后 1—1000 字符）和 `candidate_limit`（默认 20，范围 1—50）。返回 query、生效配置、候选项、入选数、候选总数、上下文字符和耗时。候选显式包含 Paragraph/Document 标识、位置、原文、三个向量/关键词/最终评分、入选状态、截断标志和排除原因；永不包含 embedding。

调试只做一次查询 Embedding 与本地评分，不调用 Chat，不创建 Conversation/Message。

### 兼容接口

```http
POST /api/knowledge-bases/{id}/search/
POST /api/knowledge-bases/{id}/chat/stream/
```

两者使用知识库持久化策略。搜索请求中旧 `top_k` 参数继续作为该次搜索的受限覆盖值（1—20），不写入配置；聊天不再固定为 5。

## 14. 页面交互与状态

知识库详情页增加：

- “检索设置”抽屉：模式、top_k、阈值、向量/自动关键词权重、上下文预算、System Prompt、资料不足提示、恢复默认和保存。
- “检索调试”抽屉：问题输入、执行按钮、生效配置摘要、统计卡片和候选列表；列表展示文档/位置、向量原始分与归一化分、关键词分、最终分、入选/排除原因，并可展开原文。

状态保留在 `KnowledgeDetailView`，因为不会跨路由共享；Pinia 继续只负责认证。调试使用递增请求序号，过期响应被丢弃；保存/调试均防重复；Prompt 和查询不写入 localStorage、URL 或持久化 Store。

## 15. 权限与安全

- 所有新接口首先按 `KnowledgeBase.id + owner=request.user` 查询。
- 候选 QuerySet 从已验证知识库向下关联 Document/Paragraph，不接受客户端 Paragraph ID。
- 调试输出、搜索引用和历史 references 使用显式字段白名单，排除 embedding、密钥、密文和内部异常。
- Query、Prompt 和无答案文本均有长度限制；文档内容放在不可信资料边界内。
- 自动化测试清空模型环境变量并 Mock 外部模型；无资料测试额外断言 Chat 客户端创建函数未调用。

## 16. 测试矩阵

覆盖提示词列出的 36 项：配置读写/owner/范围/trim/default，英中分词、空查询、BM25、余弦归一化、混合权重、稳定排序，阈值/top_k/预算/超长首项，签名和失败状态过滤，调试三个分数/无 embedding/无会话/无 Chat，正式无答案短路与持久化，自定义 Prompt/提示，正常 SSE/引用顺序/JSON 兼容，以及 Stage 03—05 和全部既有测试回归。所有网络路径使用 Mock 或本地临时服务。

## 17. 垂直切片

1. Model、Migration、配置 Serializer/API、owner 测试、设置抽屉。
2. 共享分词、BM25、余弦与混合检索、算法测试。
3. 阈值、top_k、上下文预算、超长首项和边界测试。
4. 调试 API、调试抽屉、安全/副作用测试。
5. 正式聊天、Prompt、无答案短路、SSE/引用/历史回归。

每个切片先运行相关测试和前端类型检查再继续。

## 18. 设计自审

- **兼容性：** 默认排序和数量保持；旧接口字段保留；SSE 事件和会话规则不变。
- **单一算法源：** 正式搜索、聊天和调试共用 `retrieve_candidates()`，只有输出投影不同。
- **权限：** 新 API 和候选资源都由 owner 已验证的 KnowledgeBase 向下查询。
- **性能边界：** 当前仍是 Python 全量扫描，但查询向量仅生成一次、Paragraph 一次查询、文本每候选一次分词，无 N+1；数据库级向量索引留待 pgvector 阶段。
- **阈值语义：** 统一作用最终归一化分，避免 VECTOR/HYBRID 表现不一致。
- **预算安全：** 预算按实际资料块计算，首项只截断临时 Prompt，不改数据库或调试原文。
- **无答案成本：** 在 Chat 配置解析和客户端创建前短路，并仍保留完整会话历史。
- **Stage 05：** 不改变密钥、SSRF、配置 owner 或 signature 机制；只复用其解析和过滤结果。
- **复杂度：** 不增加 Repository、搜索引擎或第三方分词/BM25 依赖。

## 19. 实际实施与验证记录

### 19.1 实际实施

- 在 `KnowledgeBase` 增加七个检索字段和四个数据库范围约束，生成并应用 additive `api.0003` Migration。
- 提取共享 `retrieval_tokenizer.py`；本地哈希 Embedding 和 BM25 使用同一中英文规则。
- 新增 `keyword_retrieval.py / retrieval.py / prompt_builder.py`；搜索、聊天和调试复用 `retrieve_candidates()`，View 不包含评分或 Prompt 拼接。
- 将原点积修正为真正的余弦相似度，加入 `[0,1]` 归一化、HYBRID 融合、稳定排序、最终分阈值、top_k 和实际 Prompt 字符预算。
- 新增检索配置与调试 API；调试响应采用显式白名单，不返回 embedding，不创建会话，也不调用 Chat。
- 无入选资料时在解析 Chat 配置和创建客户端之前短路，自定义提示仍通过 SSE 返回，并保存空 references 的完整 AI Message。
- 历史引用在原字段基础上保存 Paragraph/Document 标识、位置和三类安全评分；原前端要求的 `document_name/content/similarity` 保持兼容。
- 知识库详情页新增检索设置与调试抽屉；调试请求有序号保护，设置/查询不写入浏览器持久化状态。

### 19.2 自动化与构建证据（2026-08-23）

- 基线：后端 32 项测试通过；迁移无待生成；前端类型检查通过；生产构建通过并保留既有警告。
- 完成后：`py -3.10 manage.py test` 共 40 项全部通过，包含新增 8 组 Stage 06 功能/算法/安全测试。
- `makemigrations --check --dry-run`：`No changes detected`；`manage.py check`：无问题。
- `npm run type-check`：通过；`src` 无生成 JavaScript、无 `any`/TypeScript 忽略指令。
- `npm run build`：通过，主 JS 约 1.16 MB；只记录第三方 PURE 注释位置和 chunk 大于 500 kB 两类既有非阻断警告。
- 自动化测试显式清空模型环境变量；模型客户端使用 Mock，没有访问真实外部网络。

### 19.3 真实 HTTP 冒烟

在 `127.0.0.1:8001` 启动显式清空 Chat/Embedding 环境变量的隔离后端，使用 `stage06-smoke-http` 临时知识库完成：创建、上传、读取默认 VECTOR、保存 HYBRID、调试三类分数、确认无 embedding、阈值 1、SSE 自定义资料不足、两条消息持久化和空引用。随后删除知识库并验证级联数据与物理文件清理；未调用外部模型。

### 19.4 浏览器验收

- 使用已登录的 `demo` 本地会话创建 `stage06-browser-workbench`，通过页面文件选择器上传无敏感测试文档并显示处理成功。
- 打开设置确认默认 `VECTOR / top_k=5 / threshold=0 / weight=1 / context=6000`。
- 保存 `HYBRID / vector_weight=0.7 / keyword_weight=0.3`、自定义 System Prompt 与资料不足提示。
- 调试相同问题时页面展示原始向量分、归一化向量分、关键词分、最终分、候选/入选数、上下文字符和耗时。
- 将临时文档扩为 3 个测试切片、预算设为 1000 后，页面显示 1 个入选、2 个“超过上下文字符预算”。
- 正式提问收到本地 SSE 回答和按最终分排序的引用，URL 写入会话 ID；将阈值设为 1 后，另一个问题直接得到自定义资料不足提示。
- 刷新页面后 `HYBRID / 0.7 / threshold=1 / context=1000`、System Prompt、资料不足提示、4 条消息和第一轮引用全部恢复。
- 浏览器控制台无 warning/error。验收服务显式使用本地哈希与本地演示回答，没有调用外部 Chat/Embedding。
- 验收后精确删除临时知识库 ID 3、文档 ID 6、会话 ID 13、Paragraph、Message 和物理上传文件；`stage06-*` 残留为 0。正式后端恢复按 `.env` 启动，浏览器返回知识库列表。

浏览器第一次从隐藏 file input 触发文件选择事件超时，未计为通过；重新连接后从可见“上传文档”控件触发并成功完成页面上传。

## 20. 已知限制

- SQLite JSON 向量仍需在 Python 中扫描，适合当前轻量学习项目，不适合百万级 Paragraph。
- 轻量中文单字/二元组分词不理解词性、同义词或专业实体。
- BM25 在当前知识库候选集合内归一化，同一问题跨知识库的分数不适合直接比较。
- 上下文预算按字符而非模型 Token 计算，是无 tokenizer 依赖下的可预测近似。
- 本阶段没有外部 Reranker、查询改写、语义缓存或异步检索。
- 前端生产包仍大于 500 kB；路由懒加载和 Element Plus 按需导入属于后续性能阶段。
