# 第三阶段设计：文档管理、切片预览与安全重处理

## 1. 阶段目标

在保持登录、知识库 CRUD、文档上传、知识检索、流式问答和引用展示兼容的基础上，完成同步文档管理闭环：

- 查询当前用户知识库中的文档详情。
- 删除文档、关联切片和磁盘中的实际文件。
- 分页查看文档切片，但不向前端暴露 Embedding。
- 重新解析、切片和向量化已有文档。
- 上传和重处理共用一个文档处理 Service。
- 对正常、失败、越权和物理文件清理路径进行自动化验证。

## 2. 现状审计

### 已有能力

- `Document` 已包含 `PROCESSING / SUCCESS / FAILURE` 状态、错误信息和段落数量。
- `Paragraph` 通过外键属于 `Document`，数据库层已配置级联删除和 `(document, position)` 唯一约束。
- 上传接口支持 TXT、Markdown 和 PDF，限制 10MB。
- 上传 View 同步执行文本提取、切片、Embedding 和批量入库。
- 前端可以上传和查看简单文档列表，问答前通过成功文档判断是否可用。

### 基线验证

- `py -3.10 manage.py test`：4 个测试通过。
- `npm run type-check`：通过，`noEmit` 有效。
- `npm run build`：通过；存在既有的单个 JavaScript 包大于 500 kB 警告。
- 当前目录没有 Git 元数据，实施时使用精确补丁并逐文件复查。

### 当前缺口与风险

- 文档处理逻辑堆在 `DocumentListView.post()` 中，无法被重处理接口复用。
- 没有文档详情、删除、切片查看和重处理接口。
- Django 外键级联只清理数据库记录，不自动保证 `FileField` 对应的物理文件被删除。
- 如果重处理先删除旧切片再计算新向量，处理中途失败会破坏原来可用的数据。
- 前端只显示英文状态文本，没有错误详情和文档操作入口。

## 3. 范围与非范围

### 本阶段包含

1. 可复用同步文档处理 Service。
2. 文档详情和安全删除。
3. Paragraph 分页预览。
4. 安全重新处理。
5. 状态标签、错误提示、操作 loading 和切片抽屉。
6. 权限、失败恢复、级联文件清理与回归测试。

### 本阶段不包含

- Redis、Celery 和任务轮询。
- PostgreSQL、pgvector 和对象存储。
- OCR、扫描版 PDF 识别和更多文件格式。
- 工作空间、RBAC、历史会话界面和 Agent 工具调用。
- 文档重命名、大规模 UI 改版和无关依赖升级。

## 4. API 契约

所有接口继续使用 DRF TokenAuthentication，资源查询必须同时包含当前用户、知识库 ID 和文档 ID。不存在、跨用户或知识库与文档不匹配时统一返回 HTTP 404。

### 4.1 文档详情与删除

```http
GET /api/knowledge-bases/{knowledge_id}/documents/{document_id}/
DELETE /api/knowledge-bases/{knowledge_id}/documents/{document_id}/
```

GET 返回现有 `DocumentSerializer` 数据。DELETE 成功返回：

```json
{
  "code": 200,
  "message": "文档已删除",
  "data": true
}
```

DELETE 必须同时验证 Document、Paragraph 和物理文件均被删除。

### 4.2 切片分页

```http
GET /api/knowledge-bases/{knowledge_id}/documents/{document_id}/paragraphs/?page=1&page_size=20
```

规则：

- 默认每页 20 条，最小 1 条，最大 100 条。
- 非数字 `page_size` 回退 20。
- 按 `position` 升序返回。
- 只返回 `id / position / content`，不返回体积较大的 `embedding`。

响应：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "page": 1,
    "page_size": 20,
    "total_pages": 0
  }
}
```

### 4.3 重新处理

```http
POST /api/knowledge-bases/{knowledge_id}/documents/{document_id}/reprocess/
```

- 成功：HTTP 200，返回最新文档数据和“文档重新处理完成”。
- 失败：HTTP 400，返回最新文档数据和明确错误原因。
- 本阶段为同步处理；请求期间前端按钮显示 loading。

## 5. 文档处理 Service

新增 `api/services/document_processor.py`，统一执行：

```text
Document文件
→ extract_text
→ split_text
→ embed_texts
→ transaction.atomic
→ 删除旧Paragraph
→ bulk_create新Paragraph
→ 更新Document状态、数量和错误信息
```

上传和重处理 View 只负责资源查询、文件校验、调用 Service 和组织响应。

## 6. 状态与安全重处理策略

### 开始处理

- 记录原来是否存在切片。
- 将状态暂时设为 `PROCESSING` 并清空本次显示的旧错误。
- 旧切片暂时保留，检索仍能使用现有数据。

### 处理成功

- 文本提取、切片和 Embedding 全部先在替换事务外完成。
- 在 `transaction.atomic()` 中一次性删除旧切片并写入新切片。
- 更新状态为 `SUCCESS`，`paragraph_count` 为新切片数量，清空错误信息。
- 由于每次成功都先删除旧切片，不会重复追加。

### 处理失败

- 如果原来存在可用切片：保留旧切片，恢复为 `SUCCESS`，同步恢复真实旧切片数量，并记录“重新处理失败，已保留旧切片”的错误信息。
- 如果原来没有切片：状态设为 `FAILURE`，段落数量设为 0，记录真实错误。
- Service 在保存失败状态后重新抛出异常，由 View 转换成 HTTP 400。

这一策略的含义是：`status` 表示当前是否仍有可用于问答的切片，`error_message` 可以额外表示最近一次重处理失败警告。

## 7. 删除与物理文件清理策略

采用 Django `post_delete` Signal：

- `Document.delete()` 完成后触发文件删除。
- 删除 `KnowledgeBase` 时，级联收集并删除所有 Document，同样触发每个 Document 的 `post_delete`。
- Signal 记录存储后端和文件名，并通过 `transaction.on_commit()` 在数据库删除真正提交后清理文件，避免数据库回滚时文件已经提前丢失。

选择 Signal 而不是只在删除 View 中手动删除文件，是因为只改 View 无法覆盖知识库级联删除、后台管理删除和未来其他删除入口。

信号在 `ApiConfig.ready()` 中导入注册。测试必须分别覆盖直接删除和知识库级联删除。

## 8. 前端交互

### 文档列表

- 用中文 Element Plus Tag 显示处理状态。
- 展示文件名、段落数量、错误/警告文本。
- 提供“切片”“重处理”“删除”按钮。
- 重处理和删除分别按文档 ID 显示 loading，避免重复操作。

### 切片抽屉

- 点击“切片”打开右侧抽屉。
- 显示当前文档名称、切片序号和正文。
- 支持服务端分页，切换页码后重新请求。
- 请求失败保留抽屉并显示错误；无切片时显示空状态。

### 状态同步

- 删除成功后重新加载文档列表；如果正在预览被删除文档则关闭抽屉并清空数据。
- 重处理成功后用响应中的最新文档替换列表项；失败后重新加载文档列表，以显示 Service 保存的真实状态和错误。
- `hasDocuments` 继续由最新文档列表中是否存在 `SUCCESS` 计算，因此删除最后一个成功文档后自动禁止继续提问。

## 9. 垂直切片

1. **Service 重构：** 提取处理流程，让上传复用，先运行原 RAG 测试。
2. **详情与删除：** 后端详情/删除、Signal 文件清理、前端状态和删除操作。
3. **切片预览：** Paragraph Serializer、分页 API、前端抽屉和分页。
4. **安全重处理：** POST 接口、前端 loading、成功替换与失败保留测试。

## 10. 测试矩阵

| 场景 | 预期结果 |
|---|---|
| 查询自己的文档详情 | 200，字段正确 |
| 查询其他用户文档 | 404 |
| 使用错误知识库 ID 查询文档 | 404 |
| 切片分页 | 按 position 排序，分页元数据正确，不返回 embedding |
| 直接删除文档 | Document、Paragraph、实际文件都消失 |
| 删除知识库级联 | Document、Paragraph、实际文件都消失 |
| 重处理成功 | 新切片替换旧切片，不重复追加，状态 SUCCESS |
| 重处理失败且有旧切片 | HTTP 400，旧切片保留，状态仍可用，记录警告 |
| 重处理失败且无旧切片 | HTTP 400，状态 FAILURE，无切片 |
| 不支持的文件 | HTTP 400，不创建 Document |
| 空文本文件 | HTTP 400，Document 保留为 FAILURE 以支持重试 |
| 原 RAG 主流程 | 上传、检索、问答继续通过 |

## 11. 设计自审

- **数据安全：** 新切片准备完成前不删除旧切片；替换发生在单个数据库事务中。
- **文件安全：** 测试只使用临时 MEDIA_ROOT，Signal 的删除目标来自已经绑定到 Document 的 FileField，不拼接用户输入路径。
- **权限：** 每个嵌套资源接口都按 `knowledge_base__owner + knowledge_base_id + document_id` 查询。
- **兼容性：** 不修改模型字段和现有上传 URL，不需要数据库迁移；原上传响应格式保持不变。
- **并发边界：** 同步重处理的成功写入事务会先删除后批量写入，不产生重复位置；本阶段不解决跨进程任务锁，留给 Celery 阶段。
- **检索行为：** 处理时旧切片保留；失败且旧切片存在时恢复 SUCCESS，确保问答可继续使用旧数据。
- **复杂度：** Service 只抽取实际重复的文档处理流程，不新增仓储层或任务抽象。

## 12. 完成标准

- 本文档中的接口、状态、权限、文件清理和前端交互已实现。
- 新旧后端自动化测试全部通过。
- 前端类型检查和生产构建通过，`src` 中没有生成 JavaScript 副本。
- 真实服务完成上传、详情、切片分页、重处理、检索和删除冒烟。
- 浏览器点击验收完成；若工具环境不可用，必须准确记录替代证据和人工补验清单。
- README 和 TODO 与实际能力保持一致。

## 13. 实施与验证记录

### 实际实施

- 新增 `api/services/document_processor.py`，上传与重处理共用 `process_document()`。
- 新增文档详情/删除、Paragraph 分页和重新处理接口。
- 新增 `ParagraphSerializer`，明确排除 Embedding。
- 新增 `post_delete` Signal，并通过 `transaction.on_commit()` 同时覆盖直接删除和知识库级联删除。
- 知识库详情页新增中文状态、错误信息、操作 loading、切片抽屉、分页器、重处理和删除确认。
- README API 清单和 TODO 已同步到第三阶段实际能力。

### 自动化验证

- 实施前基线：后端 4 个测试通过，前端类型检查和生产构建通过。
- 实施后：`py -3.10 manage.py test` 共 12 个测试全部通过。
- 文件删除测试使用临时 `MEDIA_ROOT`，分别断言直接删除与知识库级联删除后物理路径不存在。
- `npm run type-check` 通过，`tsconfig` 继续保持 `noEmit`，`src` 未产生 JavaScript 副本。
- `npm run build` 通过；记录第三方 PURE 注释和单包大于 500 kB 的非阻断警告。

### 真实 HTTP 验证

使用 `stage03-smoke-*` 临时知识库完成并清理：登录、创建知识库、上传文档、查询详情、切片分页、重新处理、知识检索、删除文档和删除知识库。上传状态为 `SUCCESS`，重处理后段落数量正确，删除后列表为 0。

### 浏览器验收

- 使用已登录的 `demo` 本地会话进入 `stage03-browser-*` 临时知识库。
- 验证“处理成功”状态、25 个切片计数和三个文档操作入口。
- 打开切片抽屉，第一页展示 #1—#20；点击第 2 页后展示 #21—#25。
- 点击重新处理后，页面计数由 25 变为 1，并显示成功提示，证明前端使用最新响应更新状态。
- 提问“什么是 RAG？”后获得流式答案和 `sample-document.txt` 引用。
- 打开删除确认框，确认文案明确说明原始文件和全部切片都会清理；未在浏览器中点击最终确认。
- 通过真实 DELETE API 删除临时文档后刷新页面，确认列表为空，并验证继续提问会提示“请先上传并成功处理一个文档”。
- 所有 `stage03-*` 临时数据已清理。

浏览器自动化的系统文件选择事件两次超时，因此“通过页面文件选择器上传”未标记为通过；上传本身已由真实 multipart HTTP 冒烟和后端自动化测试覆盖。浏览器最终删除按钮因自动化安全确认规则未代替用户点击，删除接口、物理文件清理以及删除后 UI 状态分别由自动化测试、真实 HTTP 和浏览器刷新验证覆盖。

## 14. 已知限制

- 文档处理仍是同步请求；大文件会占用请求时间，也没有跨进程重处理锁。
- 进程在处理期间被强制终止时，文档可能暂时停留在 `PROCESSING`，但旧切片不会被预先删除。
- 本地文件在数据库提交后删除；若存储后端在提交后临时故障，本阶段没有异步补偿任务。
- 扫描版 PDF 不做 OCR，继续返回明确错误。
- 生产构建仍有单包大于 500 kB 警告；路由懒加载和 Element Plus 按需导入留到后续性能阶段。
