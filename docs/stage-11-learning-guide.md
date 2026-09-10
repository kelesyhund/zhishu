# 第十一阶段新手学习指南：从结构化文档到可信评测

这份文档只解释项目中已经落地的代码和当前证据。170条题目仍是`AUTO_DRAFT`，尚未经过用户人工审核，因此这里不会把它们称为正式测试集，也不会给出虚构的准确率。

## 1. 为什么解析质量决定RAG上限

RAG先从文档找证据，再让模型依据证据回答。若解析时丢了标题、页码、表格行或代码上下文，后续Embedding和大模型无法恢复这些信息。入口在`backend/api/services/document_parser.py`的`parse_document()`：它先把不同文件转换成统一Block，再由`build_chunking_plan()`切片。模型越强也无法弥补源证据缺失。

## 2. Parser、Block、Parent与Child

- Parser负责读取TXT、Markdown、PDF或DOCX。
- `DocumentBlock`是最小结构单元，带类型、标题路径、页码、顺序、哈希。
- `DocumentSection`保存Parent，即可供生成回答阅读的完整章节。
- `Paragraph`保存Child，即用于Embedding、BM25和Reranker的较小检索单元。

它们形成“文件 → Block → Parent → Child”的层次。Parent和Child分表，是为了不让不可检索Parent污染原有Paragraph分页与计数。

## 3. Markdown标题如何形成heading path

`_markdown_blocks()`维护当前各级标题。例如依次读取`# 部署`和`## 网络`，其后的正文会得到`["部署", "网络"]`。遇到另一个二级标题只替换第二级；遇到一级标题则清空更深层级。heading path随后复制到Parent、Child和检索引用，让用户知道证据位于哪个章节。

## 4. 为什么表格和代码块不能任意切断

一行表格离开表头可能无法理解，一段命令离开前置参数也可能变成错误操作。解析器把Markdown围栏代码、表格和DOCX表格识别为完整Block；切片器在安全长度内整体保留。若结构块本身超长才切分，并把警告写入`Document.parsing_warnings`，而不是默默破坏结构。

## 5. 为什么Child检索、Parent生成

Child短而聚焦，查询与它的词义或关键词更容易匹配；Parent包含标题、背景、限制条件和完整步骤，更适合送给大模型。`prepare_retrieval()`只读取`CHILD/LEGACY`，`retrieve_from_prepared()`在确定Top-K后扩展`parent_section.content`。引用仍保存命中的Child ID和`matched_content`，因此答案证据可追溯，而生成上下文更完整。

## 6. Parent扩展如何遵守预算

知识库的`max_context_chars`是硬预算。检索按最终排名逐条处理：同一Parent只加入一次；Parent能放入就使用Parent；放不下时尝试命中的Child；仍放不下则跳过或只保留可容纳内容。`RetrievalResult.context_chars`记录实际使用量。这样一个超长章节不会挤掉其他全部证据。

## 7. Gold为什么不能依赖Paragraph数据库ID

数据库ID只代表某次入库产生的行。重新切片会删旧行、创建新行，ID必然变化；用它标Gold会导致同一证据突然“消失”。本项目的Gold指向`source_id + document_sha256 + source_block_ids`，评测时按Block交集匹配，因此切片大小变化后仍能识别相同原始证据。

## 8. Stable Block ID如何生成

`_make_block_id()`综合文档稳定source_id、源文件SHA256、页码、标题路径、正文SHA256和相同内容出现序号，再计算SHA256。内容或文档版本变化会得到新ID；同一固定文件重复解析则ID相同。测试`test_txt_blocks_and_ids_are_stable`验证了这一点。

## 9. 上传、预览、重新索引的调用链

上传链路：前端上传 → `DocumentListView.post()`保存Document并创建任务 → Celery任务 → `process_document()` → `prepare_document_chunks()` → Parser/Chunker → `embed_texts()` → 事务内写Section、Paragraph和Document状态。

预览链路：前端配置表单 → `DocumentChunkPreviewView.post()` → Serializer校验 → `preview_document_chunks()` → Parser/Chunker → 返回有限的树形JSON。它不调用Embedding、不建任务、不写数据库。

重新索引链路：保存配置 → `DocumentChunkingConfigView.patch()` → 前端确认 → reindex/reprocess接口 → 与上传共用任务和`process_document()`，因此没有两套会漂移的处理逻辑。

## 10. Celery如何执行重新索引

API创建`DocumentProcessingTask`后通过`transaction.on_commit`投递任务，保证数据库提交成功后Worker才可能读取它。Worker取得文档级锁，更新stage/progress并调用处理Service。Windows学习脚本使用eager模式时任务仍走同一业务入口，只是没有真实跨进程队列；真正Redis Broker、Worker和分布式锁必须在Docker环境验收。

## 11. 为什么失败前不能删除旧切片

解析、Embedding或网络调用都可能失败。如果一开始就删旧数据，失败后知识库会从“旧答案仍可用”退化为“完全不可用”。`process_document()`先在事务外准备全部新草稿和向量，全部成功后才进入`transaction.atomic()`锁定Document并替换旧Section/Paragraph；异常时旧数据与旧signature都保留。

## 12. Vector、Weighted、RRF与Reranker

- Vector按Embedding余弦相似度寻找语义相近内容。
- BM25按词频、逆文档频率和长度归一化寻找精确词、错误码和参数。
- Weighted先归一化两路分数再按权重相加，分数尺度处理会影响结果。
- RRF只利用两路名次，用`1/(k+rank)`融合，对异构分数尺度更稳健。
- Cross-Encoder Reranker把“query + 每个候选正文”一起送入模型精排，通常更准但更慢。

## 13. “启用”不等于“实际运行”

配置`rerank_enabled=true`只表示希望使用精排。模型未安装、忙、超时、输出非法或推理异常时，`rerank_candidates()`会安全返回原候选顺序，并设置fallback code。只有`RerankOutcome.applied=true`才算真实执行。能力接口、调试响应和评测报告都分别记录配置、实际成功次数和降级次数。

## 14. Hit@K与Recall@K

Hit@K只问“前K名是否命中至少一个Gold”，每题结果是0或1。Recall@K问“全部Gold中有多少被前K找回”，即命中Gold数/Gold总数。一道多文档题有3个Gold，前5只找到1个时，Hit@5=1，但Recall@5=1/3。本项目报告同时保留分子和分母。

## 15. MRR衡量什么

MRR关注第一个相关结果出现得多早。第1名命中得1，第2名命中得1/2，第10名命中得1/10，没命中得0，再对题目求平均。它适合用户通常只需要快速看到一个有效证据的场景，但不能说明多个Gold是否都召回。

## 16. NDCG为什么需要相关度等级

相关证据并非同等重要。本项目Gold用0/1/2三级相关度，DCG采用`(2^rel-1)/log2(rank+1)`，把高相关证据放在前面奖励更多；再除以理想排序IDCG得到0到1之间的NDCG。测试明确验证分级增益，避免把它退化成普通命中率。

## 17. Citation Precision与Recall

Citation Precision = 被Gold支持的引用数 / 回答给出的全部引用数，惩罚“列很多无关引用”。Citation Recall = 已引用Gold数 / 该题全部Gold数，惩罚“关键依据没有引用”。例如给出4条引用，其中2条相关，而题目共有3个Gold，则Precision=2/4，Recall=2/3。

## 18. Faithfulness如何判断

Faithfulness检查回答中的可验证关键陈述是否都能由引用证据支持。当前评测命令接收结构化Judge结果：支持陈述数、需证据陈述总数和状态；JSON解析失败、Judge异常或缺字段都计为失败，绝不默认通过。它不能仅凭回答“听起来合理”判定。

## 19. 无答案评测为什么不能只看拒答率

一个永远回答“不知道”的系统，在无答案题上能得到100%拒答正确率，却完全不能回答正常问题。因此报告同时给出`no_answer_accuracy`和`answerable_response_rate`。只有无答案题正确拒答、可回答题正常作答，两者一起看才有意义。

## 20. Dev与Test为什么隔离

Dev用于比较切片大小、Top-K、融合权重和候选数，可以反复运行。Test用于最终验证泛化能力，配置冻结后才运行。`assert_no_cross_dataset_duplicates()`检查规范化重复和高度近似问题，防止同一道题换标点后跨集合出现。

## 21. 为什么不能根据Test继续调参

看到Test错误后调参数，本质上把Test变成了Dev，最终数字会高估真实新问题表现。正确做法是：只用Dev做选择；记录配置快照；冻结Test哈希；正式运行一次。若必须依据Test改进，应发布新版本数据集并保留旧报告，不能覆盖历史。

## 22. 数据集SHA256有什么作用

SHA256是文件内容指纹。冻结命令验证所有Test题为HUMAN_APPROVED、数量不少于100，然后写入哈希与冻结元数据。之后只要问题、Gold或顺序发生任何变化，哈希就变，报告可以识别“同名但内容不同”的测试集。哈希证明内容未变，不证明标签本身正确，所以仍需人工审核。

## 23. Mock测试与真实模型验收的区别

Mock自动化测试验证调用参数、异常、超时、排序和降级逻辑，速度快、无公网、无费用、结果稳定；但它不能证明真实权重可加载或CPU延迟。真实验收固定模型revision、权重哈希、依赖、硬件、batch和timeout。本次真实10候选测试冷启动约16.1秒，热运行约1.05秒且4/4实际执行；这只是小样本集成证据，不是正式业务P95。

## 24. 如何得到可诚实写入简历的数据

先由用户审核CSV并明确APPROVE/REJECT；导入后校验Evidence映射；冻结不少于100题的Test并记录SHA256；只用Dev确定方案；在相同知识库、Embedding、硬件、Top-K和Reranker配置下运行Vector、Weighted、RRF、RRF+Reranker；保存逐题记录、分子分母、分类指标、P50/P95和fallback；再用报告比较命令计算差值。简历只能写真实报告中的结果和样本规模，不能把170条AUTO_DRAFT或Mock测试写成“人工评测”。

## 25. JSON向量和Python扫描为什么仍是瓶颈

当前每次查询要从数据库加载全部兼容Paragraph的JSON向量，并在Python逐条计算相似度和BM25。数据量增大后，数据库传输、反序列化、内存、CPU和并发延迟都会线性增长；也缺少ANN索引、数据库过滤和横向扩展能力。下一阶段应以真实规模基准为依据迁移pgvector或检索引擎，而不是仅为了简历堆技术名词。

## 人工验收清单

在Docker可用后启动PostgreSQL、Redis、Django、Celery和Vue，使用`stage11-browser-*`临时数据依次验证：四格式上传、结构化预览无副作用、配置保存、重新索引进度、Parent/Child计数、父子检索调试、引用Child证据、预算限制、Reranker实际执行/降级、有答案/无答案问答、Dev报告和冻结后一次正式Test。验收后只清理临时浏览器数据，不删除Manifest、审核集、冻结集或正式报告。
