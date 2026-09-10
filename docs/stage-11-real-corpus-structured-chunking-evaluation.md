# 第十一阶段设计：真实语料、结构化切片与可信评测

## 1. 现状审计与基线

项目现有上传链路为 `DocumentListView -> DocumentProcessingTask -> Celery -> process_document()`。`document_parser.py` 仅支持 TXT、Markdown、PDF，把全文规范化为字符串后按约 800 字符、100 字符重叠切分；Markdown 标题、代码块、表格、PDF 页码均会丢失，DOCX 尚未列入支持范围。`Paragraph` 仅保存 position、content 和 JSON embedding，检索会把知识库内全部兼容 Paragraph 读入 Python，逐条计算向量分和 BM25。

安全重处理已经具备正确基础：新文本与向量在事务外准备，事务内锁定 Document 后替换旧 Paragraph；失败时保留旧可用切片。第八阶段已有 Celery 任务、幂等键、进度、重试、取消和文档级锁，本阶段复用它们。

第九阶段已有独立向量/BM25候选、Weighted、RRF、可选Cross-Encoder和 Hit@K、Recall@5、MRR@10、NDCG@10。但当前正式记录只有合成数据：40条测试问题、100个合成切片；RRF_RERANK 40/40降级，没有真实Reranker提升证据。标签依赖文档名、position和切片正文哈希，改变切片策略后会失效。

2026-09-09基线：后端95项测试通过；Django check无问题；0001—0007已应用且无待生成Migration；前端 `npm run type-check` 通过。沙箱内Vite构建因Windows阻止esbuild创建子进程报 `spawn EPERM`，获得进程权限后生产构建通过。Docker Desktop Linux Engine管道仍不可用，因此Redis/PostgreSQL/Celery容器验收无法执行。工作树中的项目文件全部显示未跟踪，实施按文件边界保留现有内容。

## 2. 范围与非范围

本阶段实现结构化Block、TXT/Markdown/PDF/DOCX解析、标题感知父子切片、切片配置与无副作用预览、安全重新索引、稳定Evidence ID、数据集校验/审核/冻结、分级相关性检索指标和答案级指标。继续复用Django、Vue、PostgreSQL、Redis、Celery、当前Embedding、RRF和SSE。

本阶段不引入Elasticsearch、OpenSearch、pgvector、新向量数据库、LangGraph、MCP、长期记忆、OCR、VLM、对象存储或RBAC。JSON向量/Python扫描继续作为下一阶段限制。

## 3. 业务语料与许可证

目标场景为“企业技术文档与故障排查智能助手”，优先使用Docker Engine/Compose官方公开资料。`evals/corpus/manifest.jsonl`记录稳定source_id、标题、URL、发布方、产品版本、获取时间、许可证、SHA256和本地路径。许可证不允许再分发时，raw文件不提交Git，只提交Manifest和获取方法；不抓取登录、付费或受限内容。下载器只能读取显式Manifest地址，不执行文档中的代码、宏或链接，自动化测试只使用本地Fixture。

## 4. 数据模型与Migration

Document增加稳定source_id、源文件SHA256、解析/切片配置、已索引配置签名、父切片数、解析器/切片器版本和安全警告。配置使用显式有范围约束的字段，避免任意JSON：parser_type、chunk_strategy、parent_max_tokens、child_target_tokens、child_overlap_tokens、preserve_tables、preserve_code_blocks。

Paragraph保持原模型名和 `(document, position)` 唯一约束，以兼容引用、会话、应用链路和“段落数=可检索切片数”的既有语义；它只保存 `LEGACY/CHILD` 可检索块，并增加nullable `parent_section`、heading_path、页码、token_count、content_sha256、parser/chunker版本、source_block_ids、structure_type和安全metadata。新增DocumentSection专门保存不可检索的Parent正文。父子同属一个Document由处理Service保证并由测试验证。这个分表方案是在垂直切片测试发现self-parent会破坏旧段落计数契约后作出的自审修正。

Document的 `indexed_chunking_signature` 是切片器版本和有效配置的SHA256摘要。保存新配置后旧切片仍可用，但 `needs_reprocess=true`；处理成功才更新签名。

## 5. 结构化解析和Stable Evidence ID

统一链路为：读取文件 -> 格式解析 -> DocumentBlock -> Section -> ParentDraft -> ChildDraft -> Child Embedding -> 事务内安全替换。

Block类型包括HEADING、PARAGRAPH、LIST、TABLE、CODE、QUOTE和PAGE_BREAK。字段包含block_id、type、text、heading_path、页码、source_order、content_sha256和metadata。block_id由Document source_id、源文件哈希、页码/标题路径、正文哈希与同内容出现序号确定，不依赖数据库ID或切片参数。Chunk保存其覆盖的source_block_ids，Gold Evidence据此跨切片策略保持稳定。

TXT按空行组织段落；Markdown识别标题、围栏代码、表格、列表和引用；PDF逐页提取并保留页码，全部页面无文字时明确提示扫描版PDF不支持；DOCX按正文XML顺序读取标题、段落和表格，不执行宏或嵌入对象。

## 6. 父子切片与上下文

默认新文档使用PARENT_CHILD：Parent最大1500估算Token，Child目标400，重叠60。标题边界优先于长度边界；表格和代码块在安全上限内保持整体，超长时切分并产生警告。

只有CHILD和LEGACY生成Embedding并进入召回。命中Child后保留Child作为引用证据，并用Parent正文构造生成上下文。同一Parent只加入一次；预算按最终Parent块计算；Parent放不下时退回命中Child或截取首条，不得让一个Parent无条件挤掉全部其他证据。调试输出同时显示命中Child和展开Parent信息。

## 7. 安全处理与状态

预览只读取并解析文件，绝不写Paragraph、生成Embedding、改变Document或创建Celery任务。正式上传和重新索引共用 `process_document()`。新父子草稿和全部Child向量先准备完成，随后在一个数据库事务中锁定Document、删除旧Paragraph与DocumentSection、写入新Section和Child并更新状态、数量、embedding_signature和indexed_chunking_signature。失败沿用旧规则：有旧切片则保持SUCCESS并记录失败警告；无旧切片则FAILURE。文档锁继续阻止并发重建。

## 8. API契约

- `GET/PATCH /api/knowledge-bases/{kb}/documents/{doc}/chunking-config/`
- `POST /api/knowledge-bases/{kb}/documents/{doc}/chunk-preview/`
- `POST /api/knowledge-bases/{kb}/documents/{doc}/reindex/`
- 原 `POST .../reprocess/` 保持兼容，调用相同任务入口。
- 原 `GET .../paragraphs/` 默认只返回CHILD和LEGACY，可通过专用树形预览响应查看Parent及其Children。

所有资源按当前用户、知识库和文档三重限定；不存在、越权或父子不匹配统一404。配置字段未提交表示保留，PATCH仅保存配置，不自动重建。Preview正文、Parent数和Child数均设置上限。

## 9. 前端交互

文档列表显示解析器、切片策略、Parent/Child数量、配置是否待重新索引和解析警告。切片抽屉支持普通Child分页以及父子结构预览。设置区修改显式参数后可先预览；只有“保存并重新索引”才PATCH配置并提交异步任务。按钮使用独立loading，请求序号防止切换文档后过期响应覆盖，配置和正文不进入URL或持久化存储。

## 10. 评测数据与人工边界

新格式用case_id、query、category、split、answerable、gold_evidence、answer_key_points、review_status、reviewed_at和notes。Gold Evidence引用source_id、document_sha256、source_block_ids及0/1/2相关度。AUTO_DRAFT只能进入候选包；只有用户真实确认的HUMAN_APPROVED才能冻结。智能体可生成候选和预标注，但不能将其称为人工审核。

Dev只用于调参；Test不少于100题且冻结后记录SHA256，只在配置冻结后正式执行。精确重复和明显近似问题跨split会被拒绝；Evidence缺失、哈希变化或无法映射时整次失败，禁止静默跳过。修改Test产生新版本和新哈希，旧结果继续保留。

## 11. 指标

检索继续报告Hit@1、Hit@3、Recall@5、MRR@10、NDCG@10、P50/P95和Reranker fallback。Recall@5分母是该问题全部Gold Evidence；NDCG按0/1/2使用 `(2^rel-1)/log2(rank+1)`。除总体外按category分组，并保留逐题分子、分母和排名。

答案级：Citation Precision=相关引用/全部引用；Citation Recall=已引用Gold/全部Gold；Faithfulness=有证据支持的关键陈述/需证据陈述；No-answer分别报告无答案正确拒答率和有答案正常回答率，防止全拒答作弊。LLM Judge只能作为明确标注的模型评估，必须冻结Prompt/模型/revision，结构化失败不能默认通过，未经授权不调用付费模型。

## 12. Reranker

选择支持当前语料语言、许可证明确、无需远程自定义代码的Cross-Encoder，固定模型ID/revision、依赖、硬件、batch、候选上限和超时。自动化测试使用Mock且不下载；真实集成验收必须记录实际成功次数和fallback。全部降级时不得宣称Reranker结果。现有不完整 `.models/bge-reranker-base` 不删除或覆盖。

## 13. 评测威胁模型

主要风险包括自生成标签冒充人工、Dev/Test泄漏、反复看Test调参、切片变化导致Gold漂移、缺失Evidence静默跳过、只挑最好一次、只报百分比、Reranker降级冒充提升、LLM Judge偏差、全拒答作弊、硬件/配置混合、许可证不清和日志泄密。防护分别落实为审核状态、stable block ID、严格加载失败、冻结哈希、完整配置快照、逐题报告、fallback计数、答案双指标、来源Manifest和密钥脱敏。

## 14. 测试矩阵

覆盖四类解析、标题/代码/表格/页码、稳定Block ID、父子关系、Legacy兼容、仅Child召回、Parent去重与预算、引用Child、Preview无副作用、安全替换/失败保留/并发锁、owner隔离、配置校验、Evidence映射、审核/冻结、跨split重复、分级NDCG、Recall分母、引用指标、无答案反作弊、Judge失败、Reranker实际/降级状态、报告脱敏和前十阶段全回归。测试使用临时MEDIA_ROOT、本地Fixture、Mock模型且无公网。

## 15. 设计自审

1. 保留Paragraph名称和旧接口，避免重写会话、引用和应用发布。
2. 使用独立DocumentSection而非Paragraph自关联：Parent不进入检索和段落分页，旧接口的计数、position唯一性与引用语义不变；两者都由Document级联删除，Service保证同文档关系。
3. 配置显式字段优于任意JSON，便于Serializer和数据库约束。
4. Gold指向Block而非Chunk，使切片A/B具备可比性。
5. Parent不生成向量，避免检索粒度混乱和额外费用。
6. 预览复用Parser/Chunker但不复用持久化步骤，保证无副作用。
7. 不在本阶段引入搜索引擎，以免文档质量、评测质量和存储迁移三个变量同时变化。
8. 人工审核是不可由智能体代替的证据门槛；工程完成与证据完成分别记录。

## 16. 实施与验证记录

### 16.1 实际实现

- Migration 0008新增Document结构化字段、DocumentSection和Paragraph元数据；0009先为旧Document补唯一source_id，再施加唯一约束。实际数据库已迁移到0009，`makemigrations --check --dry-run`无漂移。
- 解析器已覆盖TXT、Markdown、PDF和DOCX。Markdown保留标题/代码/表格/列表，PDF保留页码并拒绝无文本扫描件，DOCX按正文顺序读取标题/段落/表格。
- 上传与重新索引统一调用`process_document()`；Preview只执行解析和切片计划，不生成向量、不创建任务、不改变Document。
- 检索只召回CHILD/LEGACY；命中后按Parent去重扩展上下文，预算不足回退到命中的Child，引用仍指向Child稳定证据。
- 前端增加切片设置、树形预览、解析元数据、保存并重新索引以及过期响应保护。
- 数据集工具支持候选生成、审核CSV导入导出、严格验证、Dev/Test拆分、冻结SHA256、检索/答案评测和报告对比。

### 16.2 真实语料与人工门槛

- 从Docker官方文档仓库固定提交`3d15caeca7608231f930137accb6d933be157b5d`选取40份同领域Markdown，Manifest记录来源、许可证、版本、路径与SHA256。
- 独立评测知识库ID 4包含40个Document、387个DocumentSection和1200个Child Paragraph；使用本地哈希Embedding，未发生付费模型调用。
- 生成170条`AUTO_DRAFT`候选：Dev 50、Test 120；类别为精确30、语义30、步骤30、表格20、版本10、多文档30、无答案20。候选SHA256为`ee1447250020c2b8442f159e3f2d55b1165f27a70831db9080e01e998320189d`。
- `evals/reviews/stage11-review.csv`已经导出等待用户逐题审核。当前没有任何题被声称为HUMAN_APPROVED，测试集也未冻结，所以尚无可写入简历的正式准确率。

### 16.3 真实Reranker证据

- 实际模型为`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，固定revision `1427fd652930e4ba29e8149678df786c240d8825`，权重SHA256 `5daeca2481a76b5976a2bdc32f0a78532b6716da4f8cd3ff59460ef8d2f359b4`，sentence-transformers 6.0.1，CPU、batch 8、max length 512、timeout 30秒。
- 真实两候选冒烟成功并产生正确排序；10候选四次进程内基准全部`applied=true`，冷启动16118.34ms，三次热运行1045.19/1047.57/1058.46ms，观测P50为1047.57ms。样本仅三次热运行，因此最大值只能称“观测P95”，不能等同正式压测P95。

### 16.4 自动化与运行验收

- 后端112/112项全回归通过；Django system check无问题；Migration已应用到0009且无模型漂移；前端TypeScript检查和生产构建通过。生产包1690 modules，主JS约1211.46kB（gzip 395.88kB）；保留VueUse PURE注释和大chunk警告，未扩大范围。
- 真实HTTP使用临时知识库完成登录、文档列表、配置读取、无副作用预览、RRF+真实Reranker调试和重新索引任务闭环。重新索引后任务SUCCESS、进度100、2个Parent/3个Child、`needs_reprocess=false`。
- 生产Vite Preview页面返回HTTP 200并存在Vue挂载节点。当前没有浏览器点击控制，故不将抽屉、分页和按钮点击表述为已通过；仍需按本章交付清单人工点击。
- Docker Engine管道不可用，第一次真实重新索引准确返回503；本地以Celery eager+进程内锁验证业务闭环。真实Redis锁、Worker崩溃恢复和PostgreSQL并发验收仍属于环境阻塞项。

## 17. 已知限制

Docker Desktop Linux Engine管道不可用，真实Redis/Celery/PostgreSQL验收待环境恢复。当前170条为智能体候选，HUMAN_APPROVED与不少于100题的冻结测试集必须由用户真实审核后产生；在此之前不能运行或公布正式方案胜负。真实答案级评测还需要审核后的关键点和结构化答案记录。JSON向量/Python全量扫描、OCR、VLM和生产检索引擎不在本阶段。
