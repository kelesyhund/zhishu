# 知枢｜企业知识中枢

知枢是一个面向企业技术文档检索与故障排查的知识智能平台。它完成了“登录 → 组织与工作空间隔离 → RBAC授权与管理审计 → 创建知识库 → 异步上传与任务跟踪 → 切片与向量化 → 文档管理 → 模型配置 → 两路召回/RRF/可选精排 → 流式问答 → 引用展示 → 历史会话恢复 → Agent 安全工具调用与轨迹审计 → AI应用发布与第三方接入”的完整闭环。

前端品牌、信息架构和页面改造记录见 [第十二阶段：知枢品牌与前端体验改造](docs/stage-12-zhishu-frontend-redesign.md)，多租户治理设计与验证记录见 [第十三阶段：组织、工作空间、RBAC与审计](docs/stage-13-enterprise-workspace-rbac-audit.md)。

## 当前版本为什么可以零配置运行

为了优先得到三天内可运行的版本，默认使用 SQLite，并内置一个确定性的哈希向量算法。未配置大模型时，系统会把检索到的最相关原文作为演示答案。既可以继续使用环境变量，也可以在页面中为不同知识库分别选择 OpenAI 兼容的 Chat 与 Embedding 配置。

Windows简单启动脚本会启用Celery eager和进程内锁，便于无Redis学习；真正的异步上传使用阶段Compose中的Redis、Celery和PostgreSQL。向量仍保存为JSON并由Python计算，后续可按数据规模升级pgvector。

## 目录结构

```text
knowledge-chat/
├─ backend/                  Django + DRF 后端
│  ├─ config/                Django 配置和总路由
│  ├─ api/models.py          数据模型
│  ├─ api/views.py           工作空间内HTTP接口与流式响应
│  ├─ api/workspace_views.py 组织、成员、工作空间与审计接口
│  ├─ api/serializers.py     输出数据格式
│  ├─ api/tasks.py           Celery文档任务入口
│  └─ api/services/          文档解析、任务协调、向量和RAG逻辑
├─ frontend/                 Vue 3 + TypeScript 前端
│  └─ src/
│     ├─ api/                后端接口封装
│     ├─ stores/             Pinia登录与工作空间状态
│     └─ views/              登录、知识库和问答页面
├─ start-backend.ps1
├─ start-frontend.ps1
├─ docker-compose.stage08.yml Redis + PostgreSQL + Django + Worker
├─ loadtests/                 真实冒烟、竞态与Locust压测
├─ evals/                     可复现检索评测数据与JSON/CSV/Markdown报告
└─ TODO.md
```

## Windows启动

打开第一个 PowerShell：

```powershell
cd C:\Users\777\Documents\Codex\2026-07-30\w\work\knowledge-chat
.\start-backend.ps1
```

打开第二个 PowerShell：

```powershell
cd C:\Users\777\Documents\Codex\2026-07-30\w\work\knowledge-chat
.\start-frontend.ps1
```

浏览器访问：<http://127.0.0.1:5173>

登录页支持创建个人账号。注册成功后会自动登录，并自动创建该用户的个人组织和默认工作空间。企业内网部署如需关闭公开注册，可在`backend/.env`中设置：

```env
ALLOW_USER_REGISTRATION=false
```

该方式为了零基础学习启用eager模式：API契约仍返回202和Task，但Worker在请求进程内执行。要观察真实队列、跨进程Worker和分布式锁，使用：

```powershell
Copy-Item .env.stage08.example .env.stage08
# 只在本机的.env.stage08中替换临时密码和Token；该文件已被.gitignore忽略
docker compose -f docker-compose.stage08.yml up -d --build
docker compose -f docker-compose.stage08.yml ps
```

Compose后端地址为`http://127.0.0.1:18000`，完整验收和压测命令见`loadtests/README.md`。Redis和PostgreSQL不暴露宿主机端口。

演示账号：

```text
用户名：demo
密码：demo123456
```

## 配置真正的大模型

复制 `backend/.env.example` 为 `backend/.env`，填写一个 OpenAI 兼容服务：

```env
OPENAI_BASE_URL=https://你的服务地址/v1
OPENAI_API_KEY=你的密钥
LLM_MODEL=模型名称
EMBEDDING_MODEL=向量模型名称

# 数据库模型配置的独立 Fernet 主密钥（只生成一次并妥善备份）
MODEL_CONFIG_ENCRYPTION_KEY=使用下方命令生成的值

# 仅本地 Ollama/Mock 开发时设为 true；生产保持 false
ALLOW_PRIVATE_MODEL_ENDPOINTS=false
```

生成主密钥：

```powershell
py -3.10 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

主密钥不应提交到 Git；它一旦丢失，数据库中已经加密的 API Key 无法恢复。页面配置优先级为“知识库显式配置 → 环境变量 → 本地回退”。修改或切换 Embedding 后，页面会标记旧文档为“向量需更新”；重新处理成功前不会混用旧向量。

## API主链路

```text
POST /api/login/
GET /api/me/context/                                      当前用户组织、工作空间、角色与能力
GET/POST /api/organizations/                             组织列表与创建
GET/POST /api/organizations/{id}/members/                组织成员管理
GET/POST /api/organizations/{id}/workspaces/             工作空间管理
GET/POST /api/workspaces/{id}/members/                   工作空间成员管理
GET /api/audit-events/                                   当前组织审计事件（只读）
GET/POST /api/knowledge-bases/                         列表支持 keyword、page、page_size
GET/PATCH/DELETE /api/knowledge-bases/{id}/            详情、修改和删除
GET/POST /api/knowledge-bases/{id}/documents/
GET/DELETE /api/knowledge-bases/{id}/documents/{document_id}/
GET /api/knowledge-bases/{id}/documents/{document_id}/paragraphs/
POST /api/knowledge-bases/{id}/documents/{document_id}/reprocess/
GET/PATCH /api/knowledge-bases/{id}/documents/{document_id}/chunking-config/
POST /api/knowledge-bases/{id}/documents/{document_id}/chunk-preview/
POST /api/knowledge-bases/{id}/documents/{document_id}/reindex/
GET /api/knowledge-bases/{id}/processing-tasks/
GET /api/knowledge-bases/{id}/processing-tasks/{task_id}/
POST /api/knowledge-bases/{id}/processing-tasks/{task_id}/retry/
POST /api/knowledge-bases/{id}/processing-tasks/{task_id}/cancel/
POST /api/knowledge-bases/{id}/search/
GET/PATCH /api/knowledge-bases/{id}/retrieval-config/
POST /api/knowledge-bases/{id}/retrieval/debug/
POST /api/knowledge-bases/{id}/retrieval/compare/
GET /api/retrieval/capabilities/
GET /api/knowledge-bases/{id}/conversations/              会话摘要分页
GET/PATCH/DELETE /api/knowledge-bases/{id}/conversations/{conversation_id}/
GET /api/knowledge-bases/{id}/conversations/{conversation_id}/messages/
POST /api/knowledge-bases/{id}/chat/stream/
GET/POST /api/model-configs/
GET/PATCH/DELETE /api/model-configs/{config_id}/
POST /api/model-configs/{config_id}/test/
GET/PATCH /api/knowledge-bases/{id}/model-config/
GET/PATCH /api/knowledge-bases/{id}/agent-config/
GET /api/knowledge-bases/{id}/agent-runs/{run_id}/
GET/POST /api/applications/
GET/PATCH/DELETE /api/applications/{application_id}/
PUT /api/applications/{application_id}/knowledge-bases/
POST /api/applications/{application_id}/preview/chat/stream/
POST /api/applications/{application_id}/publish/
POST /api/applications/{application_id}/disable/
GET /api/applications/{application_id}/versions/
POST /api/applications/{application_id}/versions/{version_id}/rollback/
GET/POST /api/applications/{application_id}/credentials/
GET/PATCH /api/applications/{application_id}/public-access/
GET /api/applications/{application_id}/access-logs/
GET /api/public/applications/{public_token}/profile/
POST /api/public/applications/{public_token}/chat/stream/
GET /api/public/applications/{public_token}/embed/
POST /api/v1/applications/{application_id}/chat/completions
```

第二阶段知识库 CRUD、搜索与分页的设计和验收规则见
[`docs/stage-02-knowledge-crud.md`](docs/stage-02-knowledge-crud.md)。

第三阶段文档管理、切片分页、安全重处理和文件清理策略见
[`docs/stage-03-document-management.md`](docs/stage-03-document-management.md)。

第四阶段会话历史、消息分页、URL 恢复和流式持久化规则见
[`docs/stage-04-conversation-history.md`](docs/stage-04-conversation-history.md)。

第五阶段模型配置、密钥加密、SSRF 防护和 Embedding 版本隔离见
[`docs/stage-05-model-configuration.md`](docs/stage-05-model-configuration.md)。

第六阶段纯向量/混合检索、阈值、上下文预算、Prompt 配置和调试工作台见
[`docs/stage-06-rag-retrieval-workbench.md`](docs/stage-06-rag-retrieval-workbench.md)。

第七阶段 Agent 执行协议、工具安全边界、SSE 轨迹和测试验收见
[`docs/stage-07-agent-tool-calling.md`](docs/stage-07-agent-tool-calling.md)。

第八阶段Redis/Celery异步文档任务、幂等、分布式锁、删除竞态和200并发报告见
[`docs/stage-08-async-document-processing.md`](docs/stage-08-async-document-processing.md)。

第九阶段独立向量/BM25候选、RRF、可选Cross-Encoder、安全降级、A/B工作台和离线评测见
[`docs/stage-09-rag-evaluation-reranking.md`](docs/stage-09-rag-evaluation-reranking.md)。

第十阶段AI应用、草稿/发布快照、多知识库RRF、公开链接、应用API Key、iframe与访问审计见
[`docs/stage-10-application-publishing.md`](docs/stage-10-application-publishing.md)。

第十阶段面向全栈新手的代码调用链与技术原理讲解见
[`docs/stage-10-learning-guide.md`](docs/stage-10-learning-guide.md)。

第十一阶段真实公开语料、结构化解析、父子切片、稳定Evidence和可信评测边界见
[`docs/stage-11-real-corpus-structured-chunking-evaluation.md`](docs/stage-11-real-corpus-structured-chunking-evaluation.md)。

第十一阶段25个核心概念与实际调用链的新手讲解见
[`docs/stage-11-learning-guide.md`](docs/stage-11-learning-guide.md)。

## 第十一阶段可复现工作流

以下命令都在`backend`目录执行。公开语料获取方式和固定上游版本见`evals/corpus/README.md`。`stage11-candidates.jsonl`只是智能体生成的待审候选，不能直接当正式测试集：

```powershell
py -3.10 manage.py validate_evaluation_dataset --dataset ..\evals\datasets\stage11-candidates.jsonl --manifest ..\evals\corpus\manifest.jsonl --username stage11-eval-user --knowledge-base-id 4
py -3.10 manage.py export_evaluation_review --dataset ..\evals\datasets\stage11-candidates.jsonl --manifest ..\evals\corpus\manifest.jsonl --output ..\evals\reviews\stage11-review.csv
```

用户逐题检查问题、Gold Evidence和关键点，在CSV的`decision`填`APPROVE`或`REJECT`后，才可导入和拆分。`--reviewed-by`填写真实审核者标识：

```powershell
py -3.10 manage.py import_evaluation_review --review ..\evals\reviews\stage11-review.csv --output ..\evals\datasets\stage11-reviewed.jsonl --reviewed-by <审核者> --manifest ..\evals\corpus\manifest.jsonl
py -3.10 manage.py split_stage11_dataset --dataset ..\evals\datasets\stage11-reviewed.jsonl --dev-output ..\evals\datasets\stage11-dev.jsonl --test-output ..\evals\datasets\stage11-test-reviewed.jsonl --manifest ..\evals\corpus\manifest.jsonl
py -3.10 manage.py freeze_evaluation_dataset --dataset ..\evals\datasets\stage11-test-reviewed.jsonl --output ..\evals\datasets\stage11-test-v1-frozen.jsonl --manifest ..\evals\corpus\manifest.jsonl --dev-dataset ..\evals\datasets\stage11-dev.jsonl --minimum-cases 100
```

只用Dev调参；配置冻结后对Test正式运行一次：

```powershell
py -3.10 manage.py evaluate_retrieval --username stage11-eval-user --knowledge-base-id 4 --dataset ..\evals\datasets\stage11-test-v1-frozen.jsonl --manifest ..\evals\corpus\manifest.jsonl --strategies VECTOR,WEIGHTED,RRF,RRF_RERANK --output-dir ..\evals\results\stage11
py -3.10 manage.py evaluate_answers --dataset ..\evals\datasets\stage11-test-v1-frozen.jsonl --answers ..\evals\answers\stage11-test-v1-answers.jsonl --manifest ..\evals\corpus\manifest.jsonl --output-dir ..\evals\results\stage11
```

答案评测读取预先准备的结构化答案/Judge记录，不会自行调用付费LLM。未经人工审核、Test冻结和实际报告，不得在简历中填写准确率提升数字。

## 验证

后端测试：

```powershell
cd backend
py -3.10 manage.py test
```

前端类型检查和构建：

```powershell
cd frontend
npm run build
```

## 学习顺序

1. 从 `frontend/src/views/LoginView.vue` 跟踪到后端 `LoginView`。
2. 从知识库页面跟踪 `API模块 → Django URL → View → Model`。
3. 阅读文档上传接口，理解文件解析、切片、Embedding和批量入库。
4. 阅读 `services/rag.py`，理解检索、Prompt和流式输出。
5. 阅读前端 `streamChat()`，理解浏览器如何解析 SSE 数据流。
6. 跟踪历史会话抽屉到 Conversation/Message API，理解消息持久化和 URL 状态恢复。
7. 从模型配置表单跟踪到 Serializer、Fernet、模型 Client 和知识库选择，理解密钥边界与向量版本。
8. 从检索设置和调试工作台跟踪到 BM25、余弦相似度、混合评分、阈值与 Prompt Builder。
9. 从 Agent 设置跟踪 `ChatStreamView → AgentExecutor → Tool Registry → ToolContext → AgentRun/ToolExecution`，理解 Function Calling、安全工具和可审计轨迹。
10. 从上传接口跟踪 `transaction.on_commit → Redis Broker → Celery Worker → DocumentProcessor → Task轮询`，理解异步、幂等、锁、重试和取消。
11. 从检索A/B工作台跟踪 `prepare_retrieval → 两路Top-N → RRF → 可选Reranker → Top-K/预算`，并用 `evaluate_retrieval` 理解Hit@K、MRR、NDCG和延迟。
12. 从AI应用页面跟踪 `Application草稿 → ApplicationVersion快照 → 公开Token/应用Key → ApplicationRuntime → 跨库RRF/Agent → SSE与访问日志`，理解发布系统与知识库系统的职责边界。
13. 从切片设置跟踪 `Preview → Parser/Block → DocumentSection/Paragraph → Reindex`，再从审核CSV跟踪到冻结SHA256与逐题评测报告，理解RAG质量证据如何形成。

更完整的原理与升级路线见最终交付说明和 `TODO.md`。
