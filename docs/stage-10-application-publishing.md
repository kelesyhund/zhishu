# 第十阶段设计：AI应用发布与第三方接入

## 1. 现状审计与基线

当前系统以 `KnowledgeBase` 同时承载文档/Embedding、检索参数、Chat模型、Prompt与Agent配置；`ChatStreamView` 只接受一个知识库，先持久化用户消息，再检索或运行Agent，完整AI回答成功后才保存。`Conversation` 当前强制关联知识库和owner，`AgentRun/ToolExecution` 通过Conversation关联。检索统一入口为 `retrieve_candidates`，能够安全过滤失效embedding signature，但没有跨库融合。后台TokenAuthentication不适合匿名或第三方接入。工具定义尚无公开安全元数据。前端聊天状态集中在知识库详情页，可复用API类型与SSE解析思路，但应用页面应独立维护状态。

2026-09-06基线：Django check通过、76项后端测试通过、0001—0006迁移已应用且无漂移；前端type-check通过。Vite生产构建在当前受限沙箱因esbuild子进程 `spawn EPERM` 未执行；Docker Desktop引擎未启动。Git仓库没有已跟踪基线，全部工程文件显示未跟踪，因此仅修改本阶段相关文件并保留现有内容。

## 2. 范围与非范围

范围：Application CRUD、最多五个知识库绑定、草稿预览、不可变发布版本、回滚/停用、公开链接、匿名访客会话、只存摘要的应用API Key、第三方非流式/SSE调用、Redis/测试内存限流、安全访问日志、iframe嵌入、单知识库Agent应用和现有功能兼容。

非范围：可视化工作流、MCP、多Agent、RBAC、计费、pgvector、OCR、多模态、爬虫、对象存储、完整LLMOps和高风险写工具。

## 3. 知识库与应用职责边界

KnowledgeBase继续拥有文档、Paragraph、Embedding和单库检索配置；Application拥有Chat模型、Prompt、展示、Agent、工具、全局Top-K/上下文预算及发布状态。旧知识库聊天字段与接口不迁移、不删除。应用通过ApplicationKnowledgeBase多对多复用知识库。

## 4. 数据模型与ER关系

`User 1—N Application`；`Application N—M KnowledgeBase`（through ApplicationKnowledgeBase）；`Application 1—N ApplicationVersion/ApplicationCredential/ApplicationAccessLog`；`Application 1—1 ApplicationPublicAccess`；Conversation增加可空application/application_version、visitor_id_hash、access_type和updated_at。旧会话仍为knowledge_base+owner；预览会话为application+owner；公开/API会话为application+visitor_id_hash。

Application删除只级联应用版本、绑定、凭证、访问配置、日志与应用会话，不删除知识库、文档或模型。KnowledgeBase被应用绑定时由PROTECT和View冲突映射阻止删除。

## 5. Migration与旧数据兼容

新增模型和Conversation可空字段由Django生成Migration。旧Conversation行保持原值并满足兼容约束。本阶段不进行数据搬迁，不删除KnowledgeBase已有配置，不手工修改数据库。

## 6. 草稿、发布、快照与回滚

草稿直接读取Application。发布在事务中锁定Application，以 `max(version)+1` 建立不可变JSON快照并切换current_published_version；失败保留旧指针。快照记录协议版本、Chat配置ID/revision、Prompt、展示/Agent配置、有序知识库ID/权重与检索摘要，不记录任何密钥或文档内容。旧会话固定使用创建时版本，新会话使用当前版本。回滚只切换版本指针。

## 7. 多知识库检索与跨库RRF

每个知识库独立调用现有 `retrieve_candidates`，因此不同Embedding维度永不直接比较。跨库按 `weight/(60+rank)` 聚合排序，再应用应用级Top-K和上下文预算。引用增加knowledge_base_id/name。失效signature继续在单库入口排除。第一版最多五库并顺序执行，降低线程/数据库连接复杂度。

## 8. 运行时与Service职责

- application_runtime：解析草稿或版本快照并验证资源、模型revision。
- application_publishing：发布校验、原子版本递增、回滚与停用。
- application_retrieval：单库复用、跨库Weighted RRF、全局预算。
- application_credentials：高熵Token创建、摘要验证、过期/禁用。
- application_public_access：公开Token、访客签名、会话隔离。
- application_rate_limit：Redis固定窗口和测试内存实现。
- application_access_logs：仅记录请求元数据与安全错误码。

View只负责认证、资源查询、Serializer、Service调用与响应/SSE组织。

## 9. API契约

后台：`/api/applications/`、`/{id}/`、`/{id}/knowledge-bases/`、`/{id}/preview/chat/stream/`、`/{id}/publish/`、`/{id}/disable/`、`/{id}/versions/`、`/{id}/versions/{version}/rollback/`、`/{id}/public-access/*`、`/{id}/credentials/`、`/{id}/access-logs/`。

公开：`/api/public/applications/{token}/profile|visitor|chat/stream|embed|conversations/{id}/messages/`。

第三方：`/api/v1/applications/{id}/chat/completions`，只实现messages最后一条user内容、stream和conversation_id最小子集。普通JSON沿用 `{code,message,data}`；SSE沿用meta/content/references/done/error，并可包含安全的Agent事件，不输出隐藏思维链。

## 10. 凭证与匿名身份

应用Key格式 `kc_app_<prefix>_<secret>`，公开Token格式 `kc_pub_<prefix>_<secret>`；secret使用secrets生成，数据库只存SHA-256摘要、prefix和last4，常量时间比较，明文仅创建/轮换响应一次。visitor_token由Django signing签名，包含application和随机nonce并限时，前端仅放公开页sessionStorage，数据库只保存nonce摘要。

## 11. 限流、iframe、CSP与XSS

公开客户端默认20次/分钟、API凭证60次/分钟、应用300次/分钟；测试使用可重置内存后端，生产Redis异常时拒绝外部调用。客户端只信REMOTE_ADDR，受信代理另行显式配置。`/embed/` 返回独立嵌入容器并设置 `Content-Security-Policy: frame-ancestors`，allowed_frame_origins只接受规范化http(s) origin。Vue文本绑定默认转义，公开页不使用 `v-html`。Django访问/错误日志配置 `ApplicationSecretFilter`，把URL中的完整公开Token和意外出现的应用Key替换成 `<redacted>`；生产反向代理仍需配置同等脱敏规则。

## 12. 工具公开规则

ToolDefinition增加allow_public、side_effect_level、requires_owner。应用Agent复用原 `AgentExecutor`、`AgentRun` 和 `ToolExecution`；发布时强制只绑定一个知识库。公开/API会话运行时再次按三项安全元数据过滤工具，只允许 `allow_public=True`、无副作用且不依赖owner的工具。模型发起未注册、未启用或不允许公开的调用仍会被拒绝并写审计记录。多知识库Agent明确拒绝发布，不会任意挑选一个知识库。

## 13. 权限、删除与生命周期

所有后台Application嵌套资源同时过滤owner和父资源。公开/API只能读取已发布且未停用版本。直接删除PUBLISHED返回409，先disable后删除。删除应用级联应用数据但保留KB/Document/Paragraph/ModelConfig。被绑定KB删除返回409。不存在、越权与父子不匹配统一404。

## 14. 页面交互

新增 `/applications` 列表、`/applications/:id` 编辑/预览、`/applications/:id/overview` 发布/版本/凭证/日志、`/share/:token` 公开聊天。密钥只在一次性弹窗展示；后台Token与visitor token隔离；过期响应不得覆盖新应用。

## 15. 测试矩阵

覆盖owner隔离、跨用户KB/模型绑定、五库限制、删除保护、草稿/快照隔离、发布并发/失败保留、回滚父子校验、快照无密钥、模型revision、跨库RRF/预算/signature、公开Token摘要/轮换/停用、API Key摘要/过期/禁用/跨应用、访客跨应用和会话隔离、消息持久化、公开工具限制、429与Retry-After、日志脱敏、XSS/CSP以及前九阶段全回归。自动化模型调用全部Mock。

## 16. 设计自审

1. 不搬迁旧字段，避免阶段范围失控；应用逻辑作为新垂直链路接入。
2. JSON快照冻结配置但不冻结知识内容，符合知识库持续更新语义。
3. 摘要而非加密保存外部凭证，因为只需验证无需恢复。
4. 多库按排名融合，避免跨模型相似度不可比。
5. 公共Agent工具默认拒绝，安全能力不足时不静默降级。
6. 固定窗口限流足以满足本阶段，不引入复杂网关。
7. OpenAI兼容只承诺最小子集，避免虚假兼容。

## 17. 实际实施与验证记录

实现内容：

- 数据层：新增 `Application`、`ApplicationKnowledgeBase`、`ApplicationVersion`、`ApplicationCredential`、`ApplicationPublicAccess`、`ApplicationAccessLog`，扩展 `Conversation` 应用/版本/匿名访客作用域。
- 服务层：新增发布快照、运行时解析、跨库RRF、凭证摘要、公开访客、限流、访问日志和应用会话Service；现有Agent执行器适配应用草稿/正式快照。
- API层：完成后台CRUD/绑定/预览/发布/停用/回滚/凭证/公开配置/日志，公开资料/访客/聊天/消息/iframe，以及OpenAI兼容最小子集。
- 前端：新增应用列表、草稿配置与预览、发布概览、公开聊天四个页面；实现一次性密钥、版本、白名单、凭证和日志交互。
- Migration：`0007_conversation_access_type_conversation_updated_at_and_more.py`，本机已应用，`makemigrations --check --dry-run` 为 `No changes detected`。
- 自动化：最终全量后端95项测试通过；新增阶段测试覆盖owner隔离、快照/回滚、删除边界、摘要凭证、Token轮换、匿名会话隔离、跨应用拒绝、日志脱敏、iframe CSP、跨库RRF、限流和应用Agent复用（含普通JSON与SSE）。前端 `npm run type-check` 通过。
- 构建：标准 `npm run build` 在当前Codex Windows沙箱因esbuild创建子进程被系统拒绝（`spawn EPERM`）；使用只替换构建转换器、不修改业务源码的无子进程路径完成生产打包，1690个模块构建成功，产物在 `frontend/dist-sandbox-stage10`。依赖文件的临时修改已恢复。
- 浏览器：在 `http://127.0.0.1:5175` 实际完成登录态进入应用列表、创建临时应用、绑定知识库、保存草稿、SSE预览失败提示、发布v1、公开页加载、访客会话刷新恢复、iframe嵌入显示。当前环境中已配置的外部模型无法连接，页面返回安全错误“无法连接模型服务，请检查地址和网络”，未将失败冒充成功。验收过程中发现并修复Django访问日志输出完整公开Token的问题，复验日志仅显示 `kc_pub_<redacted>`。`stage10-browser-acceptance`临时应用及关联版本、Token、会话、消息和日志已经按精确名称清理，知识库/文档保持不变。

## 18. 已知限制

向量仍为JSON/Python扫描；多库顺序检索；应用Agent只支持单知识库且不支持高风险公开工具；公开Token仅显示一次；第三方兼容接口只实现常用最小子集；无工作空间/RBAC；无复杂跨节点工作流；生产反向代理Token脱敏、生产级网关、集中日志和费用统计延期。外部模型真实成功响应需在网络和配置可用的部署环境补验。
