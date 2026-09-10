# Stage 09离线检索评测

`datasets/`由`stage09_evaluation_data --setup`生成60条开发集和40条冻结测试集。语料是可识别、非敏感的合成工程知识，只验证评测链路与固定语料表现，不能表述为真实用户准确率。

```powershell
cd backend
py -3.10 manage.py stage09_evaluation_data --setup --dataset-dir ..\evals\datasets
py -3.10 manage.py evaluate_retrieval --username stage09-eval-user --knowledge-base-id <输出ID> --dataset ..\evals\datasets\stage09-dev.jsonl --strategies VECTOR,WEIGHTED,RRF,RRF_RERANK --output-dir ..\evals\results
py -3.10 manage.py stage09_evaluation_data --cleanup
```

正式报告同时生成JSON、CSV和Markdown。模型未配置时，`RRF_RERANK`会如实记录为降级，而不是假装运行了Cross-Encoder。

当前仓库中的`stage09-dev.jsonl`与`stage09-test.jsonl`分别为60/40条合成样本。测试集报告即使重复生成也不得用于调参；报告中的`strategy_configs`、数据集SHA256、Embedding签名、逐题排名和降级率用于复核运行条件。

真实本地模型需要先将可信权重放到Git忽略的目录，再设置`RERANKER_MODEL_PATH`。默认`RERANKER_ALLOW_DOWNLOAD=false`，自动化测试不会下载模型或访问外网。

## Stage 11真实语料与可信评测

Stage 11把场景收窄为Docker Engine/Compose技术文档与故障排查助手。语料Manifest位于`corpus/manifest.jsonl`，固定40个官方文档来源、上游提交、许可证、SHA256和本地相对路径；获取及再分发规则见`corpus/README.md`。

当前文件状态：

- `datasets/stage11-candidates.jsonl`：170条程序候选，全部为`AUTO_DRAFT`。
- `reviews/stage11-review.csv`：供用户逐题审核的CSV。
- 尚无`HUMAN_APPROVED`冻结Test，也没有正式准确率报告。

审核者必须检查问题是否自然、Gold Block是否真正相关、0/1/2相关度和关键点是否正确，然后明确填写`APPROVE`或`REJECT`。程序不会把空decision、自生成标签或不足100题的Test升级成正式冻结集。完整可复制命令见项目根目录`README.md`。

正式报告包含数据集/Manifest SHA256、语料与切片数量、检索和Reranker配置、硬件、逐题分子分母、分类指标、P50/P95及实际fallback次数。`RRF_RERANK`只有在`reranker_applied=true`时才计作真实精排；未配置、繁忙、超时或非法输出都会如实记录安全降级。

答案级命令读取预先准备的结构化答案记录，计算Citation Precision、Citation Recall、Faithfulness、No-answer Accuracy和Answerable Response Rate。Judge失败不能默认通过，程序也不会自动调用付费LLM。
