# 第九阶段设计：RAG评测、RRF融合与Cross-Encoder精排

## 1. 现状审计与基线

当前 `retrieve_candidates()` 一次读取知识库全部状态成功且Embedding签名兼容的Paragraph，在Python中为每条切片同时计算余弦分和BM25分。`VECTOR`直接使用归一化余弦分，`HYBRID`使用向量分与BM25归一化分的线性加权；随后统一执行阈值、Top-K和字符预算。普通搜索、聊天、检索调试和Agent `knowledge_search` 已复用同一入口，这是本阶段继续演进而不复制算法的基础。

当前没有独立两路Top-N、RRF、Cross-Encoder、A/B对比、版本化评测数据或评测命令。调试接口不返回embedding，Embedding signature在候选QuerySet阶段过滤，Message只保存公开引用。向量仍为JSON并由Python全量扫描，规模优化留待pgvector阶段。

2026-09-01基线：后端69项测试通过，`manage.py check`无问题，0001—0005均应用且无待生成Migration；前端`vue-tsc -b`通过。Vite生产构建在当前沙箱因esbuild子进程`spawn EPERM`未启动，这属于执行环境限制，阶段结束时需在允许子进程的环境补验。工作区没有Git跟踪基线，所有项目文件均显示未跟踪，因此实施只修改本阶段相关文件并记录清单。

## 2. 范围与非范围

本阶段包含独立向量/BM25候选、RRF、可选本地Cross-Encoder适配器、安全降级、能力接口、检索A/B对比、离线评测服务/命令、前端设置与排名链路、合成开发/冻结测试集和真实模型验收。

不包含pgvector、搜索引擎、远程Reranker、查询改写、HyDE、Multi-Query、语义缓存、OCR、对象存储、RBAC、MCP和大规模UI重构。

## 3. 数据模型与兼容性

KnowledgeBase新增：

- `fusion_method`: `WEIGHTED/RRF`，默认`WEIGHTED`。
- `vector_candidate_k`: 1—100，默认30。
- `keyword_candidate_k`: 1—100，默认30。
- `rrf_k`: 1—200，默认60。
- `rerank_enabled`: 默认false。
- `rerank_candidate_k`: 1—50，默认20。

`retrieval_mode=VECTOR`时忽略融合方式；`HYBRID+WEIGHTED`完整保留旧公式；`HYBRID+RRF`才启用两路Top-N和RRF。Reranker只允许与RRF组合。旧行通过数据库默认值保持原行为，Migration只增加字段与范围约束。

## 4. 两路召回与RRF

同一查询只生成一次Embedding、只读取一次Paragraph。每条Paragraph只分词和计算一次。向量列表按归一化向量分降序、Paragraph ID升序；BM25只让原始分大于0的候选进入关键词列表，按BM25降序、向量分降序、ID升序。

RRF从1开始排名：

```text
score(p) = Σ 1 / (rrf_k + rank(p, list))
normalized(p) = score(p) / max(score)
```

合并以Paragraph ID去重，排序为RRF降序、向量rank、关键词rank、ID。RRF模式的0—1阈值作用于单次查询内的归一化RRF分；WEIGHTED模式继续作用于线性加权分。二者都只适合本次查询，不能跨查询解释绝对相关度。

## 5. Cross-Encoder接口、超时与降级

本阶段采用系统级本地模型而不是新增用户密钥。环境变量提供模型引用、设备、最大输入长度、batch、超时和下载开关。生产默认禁止运行时下载；模型权重与缓存不提交Git；加载时固定`trust_remote_code=False`。

模型按Django进程懒加载。推理使用单槽有界执行器：任务占用信号量直到真实Future结束，即使调用方超时也不会继续无限提交后台推理。超时、忙碌、未配置、加载失败、非法数量、NaN/Infinity统一降级为RRF顺序，并输出安全代码，不返回路径、traceback或原始异常。

Reranker只接收通过基础阈值后的前`rerank_candidate_k`条，原始分只用于本次查询内排序，不和RRF相加、不套用相似度阈值。重排后再执行最终Top-K和上下文预算。

## 6. Service与调用链

- `vector_retrieval.py`：余弦候选与稳定rank。
- `keyword_retrieval.py`：BM25原始/归一化分与稳定rank。
- `rrf.py`：去重、RRF和稳定排序。
- `reranker.py`：能力、懒加载、有界执行、输出验证和降级。
- `retrieval.py`：一次准备候选，按设置排名、阈值、重排和预算。
- `retrieval_evaluation.py`：JSONL校验、指标、运行记录和导出。

View只完成owner查询、Serializer校验、调用Service和响应。搜索、聊天、调试、比较和Agent工具继续复用`retrieval.py`。

## 7. API契约

保留现有检索配置/调试/搜索/聊天接口，配置响应增加六个字段。

```http
GET /api/retrieval/capabilities/
POST /api/knowledge-bases/{knowledge_id}/retrieval/compare/
```

能力接口只返回RRF可用、Reranker是否配置/就绪、安全模型显示名和设备，不加载模型、不返回绝对路径。

比较接口以知识库当前配置为baseline，以请求内实验配置为experimental；不持久化、不调用Chat、不创建会话/消息。候选读取、分词和Query Embedding复用一次。所有知识库查询限定owner，不存在或越权统一404。

调试候选增加vector/keyword/RRF/pre-rerank/rerank/final rank及分数、是否降级和阶段耗时；不适用字段为null，永不返回embedding。

## 8. 页面交互

检索设置增加融合方式、两路候选数、RRF k、Reranker开关和重排候选数；能力不可用时禁用开关并解释原因。调试抽屉展示各阶段rank、原始/归一化分、降级与耗时。A/B区域用当前配置对比临时RRF配置，高亮新增、丢失和名次变化；请求不写URL/localStorage/Pinia，并使用请求序号阻止旧响应覆盖。

## 9. 评测协议

数据集使用JSONL，目标由`document_name + position + content_sha256`定位，避免把数据库自增ID当稳定标签。加载时任何目标缺失、重名或哈希变化都使整次评测失败，禁止静默跳过。

提供60条合成开发集和40条冻结合成测试集，明确标注`synthesis=true`。合成结果只能说明该固定语料上的离线检索表现，不能表述为真实用户准确率。测试集只在配置冻结后运行一次；如本阶段为验证实现而重复运行相同配置，必须记录而不能称为重新调参。

指标为Hit@1、Hit@3、Recall@5、MRR@10、NDCG@10、P50/P95和Reranker fallback rate。报告同时保存逐题结果、分子/分母、数据集SHA256和完整策略快照，输出JSON、CSV、Markdown。管理命令只读被指定owner的知识库，不接受API远程文件路径。

## 10. 安全与资源威胁模型

| 风险 | 控制 |
|---|---|
| 越权调试/比较 | knowledge_id + owner查询 |
| embedding/路径泄漏 | 显式输出白名单与安全模型名 |
| 超长输入/候选耗尽CPU | Query、正文、候选、batch硬上限 |
| 超时线程堆积 | 单槽执行器、信号量在Future完成后释放 |
| 恶意模型代码 | 本地路径、默认禁下载、trust_remote_code=false |
| Reranker异常中断聊天 | 统一降级到RRF |
| 向量版本混用 | signature在候选准备前过滤 |
| 评测污染/造数 | dev/test分离、哈希、逐题原始结果和样本数 |

## 11. 测试矩阵

覆盖默认兼容、两路Top-N、RRF公式/去重/同分/归一化/空集、维度与signature、Reranker候选上限/超时/忙碌/非法输出/降级、预算顺序、引用顺序、安全输出、能力接口、A/B无持久化/无消息/owner、四项指标、数据缺失与哈希变化、Agent共用链路、无外网/无模型下载和全部旧功能回归。

## 12. 设计自审

1. 默认WEIGHTED而非迁移到RRF，避免升级改变现有答案。
2. 比较接口复用PreparedRetrieval，避免同一问题生成两次Embedding。
3. Reranker只重排有限候选，成本和失败边界可控。
4. Reranker原始分不参与阈值或跨模型比较，避免错误归一化。
5. 单槽执行器承认线程无法被强杀，以背压而不是无限线程掩盖超时。
6. 评测命令与在线API分离，避免用户通过API读取服务器任意路径或触发大批量模型成本。
7. 本阶段质量优先，仍保留Python全量扫描；pgvector只替换候选获取层，不影响RRF、重排和评测层。
8. 如果冻结测试集没有提升，新策略仍交付但保持默认关闭，并如实记录负面结果。

## 13. 指标公式与性能预算

- Hit@K：前K条中只要存在一个相关切片即记1。
- Recall@K：前K条命中的相关切片数除以该问题全部相关切片数。
- MRR@10：第一个相关切片名次的倒数；前10条未命中为0。
- NDCG@10：按`1/log2(rank+1)`累计相关性收益，再除以理想排序收益。
- P50/P95：对逐题端到端检索毫秒数使用nearest-rank百分位。
- fallback rate：Reranker降级次数/总问题数。

报告必须同时保留分子、分母和逐题行。RRF目标是不增加外部调用；本地CPU Top-20重排的交互P95目标为2秒，流式首事件总P95目标为3秒。未取得真实模型数据前不得声称满足这两个目标。

## 14. Migration与实际实施

Django正常生成并应用`0006_knowledgebase_fusion_method_and_more.py`，只新增六个配置字段及范围CheckConstraint。核心实现分布在`vector_retrieval.py`、`keyword_retrieval.py`、`rrf.py`、`reranker.py`、`retrieval.py`和`retrieval_evaluation.py`；搜索、聊天、调试、比较和Agent工具仍从统一检索入口调用。

新增能力接口与A/B接口；前端设置抽屉增加RRF/Reranker配置、能力状态、逐阶段排名、耗时、降级提示和临时策略对比。A/B只比较最终Top-K入选集合，不写KnowledgeBase，也不创建Conversation/Message。前端保存非RRF模式时会主动关闭`rerank_enabled`，避免隐藏字段造成非法组合。

Reranker依赖固定为`sentence-transformers==6.0.1`。本地加载设置`trust_remote_code=False`与`local_files_only=True`（除非明确开启开发下载），输入、候选、batch和超时均有硬上限。单工作线程与单槽信号量保证超时任务结束前后续请求直接降级，不会无限创建推理线程。

## 15. 离线开发集与冻结测试集结果

最终可复现报告：

- 开发集：`evals/results/retrieval-evaluation-20260901T154104Z.{json,csv,md}`；SHA256 `90d313f7436b67bd517d3fea65dc889f27d0114ffabaef4b5894b7a04c924085`。
- 冻结测试集：`evals/results/retrieval-evaluation-20260901T154108Z.{json,csv,md}`；SHA256 `354b741b2e37f9eabb3b56c44a0a2956ea2ec3bc50bbc02843e0ff0edfe781a0`。
- 语料：10份合成文档、100个合成切片；开发60问、测试40问；本地哈希Embedding，legacy signature。
- 配置：Top-K=5，两路候选各30，RRF k=60，Reranker候选20，阈值0。

| 集合/策略 | Hit@1 | Hit@3 | Recall@5 | MRR@10 | NDCG@10 | P50/P95 | 降级 |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev VECTOR | 34/60 | 52/60 | .9333 | .7306 | .7965 | 28/32ms | 0/60 |
| dev WEIGHTED | 60/60 | 60/60 | 1.0000 | 1.0000 | 1.0000 | 28/32ms | 0/60 |
| dev RRF | 36/60 | 56/60 | 1.0000 | .7772 | .8339 | 28/32ms | 0/60 |
| dev RRF_RERANK | 36/60 | 56/60 | 1.0000 | .7772 | .8339 | 28/32ms | 60/60 |
| test VECTOR | 9/40 | 16/40 | .4500 | .3125 | .3471 | 27/31ms | 0/40 |
| test WEIGHTED | 10/40 | 10/40 | .2500 | .2500 | .2500 | 27/31ms | 0/40 |
| test RRF | 9/40 | 11/40 | .2750 | .2738 | .3145 | 27/31ms | 0/40 |
| test RRF_RERANK | 9/40 | 11/40 | .2750 | .2738 | .3145 | 27/31ms | 40/40 |

这些是专为验证链路构造的合成结果，不是生产准确率。RRF在冻结集Hit@1比WEIGHTED少1题，但MRR/NDCG更高；它改善了8题的首个相关结果名次，却在`stage09-075`把目标从第1移到第2。原因是RRF只使用名次，放弃了该样本中线性分差携带的信息。因此默认继续保持WEIGHTED，RRF只是可选实验策略。

冻结集曾在报告字段补全与BM25同分规则修复前执行过一次，最终报告又执行一次；没有依据测试集调节候选数、权重、k或阈值，但这不符合最严格的“一次性盲测”流程。后续真实业务评测应建立新的不可见测试集。

## 16. 真实Cross-Encoder状态

计划模型为`BAAI/bge-reranker-base`，官方提交`2cfc18c9415c912f9d8155881c133215df768a70`，MIT许可，目标设备CPU。依赖已安装且`CrossEncoder`导入成功；模型元数据与许可已核对。

权重没有完成下载和推理：Hugging Face大文件通道在单个权重临时文件下载到约770MB后停止增长，普通HTTP续传又连续连接超时；本地目录与Hub缓存/预分配文件合计约2.4GB。因此没有模型校验值、真实Top-20 P50/P95、内存变化，也没有任何“Reranker提升”结论。所有`RRF_RERANK`离线行均以100% fallback明确表明只执行了RRF。失败的`.models`缓存被Git忽略；即使取得该目录写权限，当前执行策略仍阻止删除命令，需人工清理。

网络恢复后的补验步骤：下载固定提交的`safetensors`和Tokenizer文件到`.models/bge-reranker-base`；保持`RERANKER_ALLOW_DOWNLOAD=false`；设置本地路径；先执行一问两候选冒烟，再运行开发集；冻结新的测试集后只运行一次，并记录模型文件SHA256、CPU、Top-20 P50/P95和进程内存增量。

## 17. 自动化、真实HTTP与浏览器验收

- 新增Stage09测试7项，覆盖公式、默认兼容、跨字段校验、有限候选重排、非法输出/NaN/超时/忙碌降级、A/B owner与无持久化、数据哈希和指标。
- 完整后端76项测试通过；`manage.py check`无问题，`makemigrations --check --dry-run`无待生成变更，0001—0006均已应用。
- 前端`vue-tsc -b`通过，无TypeScript错误和src JavaScript副本。
- 真实HTTP使用`stage09-http-*`临时数据完成：登录、能力、默认WEIGHTED、A/B、PATCH到RRF、未配置降级、调试无embedding、搜索、SSE meta/references/done均返回成功。
- 浏览器实际打开`http://127.0.0.1:5173/knowledge`，但Vite加载源码时被当前沙箱禁止创建esbuild子进程，页面显示`spawn EPERM`覆盖层；因此UI点击、控制台业务检查和视觉验收未通过，不能描述为已验收。
- `npm run build`和`vite build --configLoader native`同样在创建子进程时失败；类型检查证实的只是类型正确，不等于生产构建通过。
- Docker CLI配置可读，但Docker Engine命名管道返回Access denied，未执行容器内模型资源验收。
- `stage09-eval-user`与`stage09-http-user`及其知识库、11份文档、102个切片、1个会话、2条消息均已删除；复查所有`stage09-*`数据库计数和MEDIA_ROOT物理文件均为0。离线JSONL与结果报告作为交付物保留。

人工浏览器补验：在普通PowerShell启动前后端；登录；进入带文档知识库；检查旧库WEIGHTED；切换RRF；调试排名；执行A/B；配置本地权重后启用Reranker并确认名次变化；临时改错模型路径确认降级仍能搜索/聊天；刷新确认设置；检查控制台；删除`stage09-browser-*`。

## 18. 代码复查结论

复查确认两路Top-N独立、RRF按rank而非原始分融合、Paragraph按ID去重、Query Embedding/A-B候选只准备一次、signature和SUCCESS过滤在评分前、Reranker只接收有限候选、异常不打断正式问答、A/B不持久化、能力/调试不暴露绝对路径或embedding。范围内修复包括BM25同分时使用向量分再以ID稳定排序、A/B只比较实际入选Top-K、Reranker候选不超过两路理论并集、前端离开RRF时关闭隐藏的重排开关。

## 19. 已知限制

- JSON向量与Python全量扫描不适合百万级Paragraph。
- 本地Reranker会占用每个Web进程的独立内存；生产规模需拆为独立推理服务。
- Python线程超时不能终止底层推理，只能通过背压防止堆积。
- 合成评测不代表真实业务分布，简历必须标注数据集性质与样本数。
- 当前没有真实Cross-Encoder运行数据，Reranker保持默认关闭。
- 当前沙箱未完成生产构建和浏览器点击验收，需在允许esbuild子进程的环境补验。
