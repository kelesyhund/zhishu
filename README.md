# 知枢｜企业知识中枢

[![CI](https://github.com/kelesyhund/zhishu/actions/workflows/ci.yml/badge.svg)](https://github.com/kelesyhund/zhishu/actions/workflows/ci.yml)

知枢是面向企业技术文档与故障排查场景的知识智能平台，提供从文档治理、混合检索、可信问答到 AI 应用发布的完整能力。系统采用组织与工作空间隔离，支持私有模型配置、异步文档处理、引用溯源和运行审计。

## 核心能力

- **企业知识治理**：组织、工作空间、成员角色、RBAC 权限和审计日志。
- **文档处理流水线**：TXT、Markdown、PDF、DOCX 解析，结构化切片、父子切片、预览、重处理和版本签名。
- **可信 RAG**：BM25 与 Embedding 混合召回、RRF 融合、可选 Cross-Encoder 精排、上下文预算和引用溯源。
- **模型配置管理**：Chat 与 Embedding 独立配置，知识库级绑定、连通性测试、Fernet 密钥加密及 SSRF 防护。
- **会话与应用发布**：SSE 流式问答、会话持久化、多知识库应用、版本发布、公开链接和兼容 OpenAI 的 API。
- **异步与可观测性**：Redis、Celery、任务幂等与重试，Prometheus、Grafana、OpenTelemetry、结构化日志和健康检查。

## 系统架构

```text
Browser
   │
   ▼
Nginx ───────────────► Vue 3 SPA
   │
   ▼
Django REST Framework ─────► OpenAI-compatible Models
   │            │
   │            ├──────────► Redis ──► Celery Worker
   ▼            │
PostgreSQL      └──────────► OpenTelemetry / Prometheus / Grafana
```

| 层级 | 技术栈 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Pinia、Vue Router、Element Plus、Axios |
| 后端 | Python 3.10+、Django 5、Django REST Framework、Gunicorn |
| 数据与任务 | PostgreSQL、Redis、Celery |
| AI 与检索 | OpenAI Compatible API、BM25、Embedding、RRF、Sentence Transformers |
| 交付与运维 | Docker Compose、Nginx、Prometheus、Grafana、OpenTelemetry、GitHub Actions |

## 快速部署

### 生产拓扑

准备 Docker Engine，复制配置模板并替换全部占位密钥：

```powershell
Copy-Item .env.production.example .env.production
docker compose -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml --profile ops run --rm migrate
docker compose -f docker-compose.production.yml up -d
```

生产环境必须配置可信域名与 HTTPS。仅进行本机验收时，可叠加 `docker-compose.production.local.yml` 并访问 `http://127.0.0.1:18080`。

### 本地开发

```powershell
# 终端一：后端
./start-backend.ps1

# 终端二：前端
./start-frontend.ps1
```

默认访问地址：`http://127.0.0.1:5173`。登录页支持注册；公开注册可通过 `ALLOW_USER_REGISTRATION` 控制。

## 配置

关键配置见 [`backend/.env.example`](backend/.env.example) 和 [`.env.production.example`](.env.production.example)。

模型解析优先级：

```text
知识库显式配置 → 系统环境变量 → 内置检索降级
```

数据库中的模型 API Key 使用独立 `MODEL_CONFIG_ENCRYPTION_KEY` 加密。该主密钥不得提交到 Git，丢失后已加密的模型密钥无法恢复。Embedding 配置变化会使不兼容文档进入待重处理状态，旧向量不会被静默混用。

## 运行状态

| 接口 | 用途 |
| --- | --- |
| `/health/live/` | 进程存活检查 |
| `/health/ready/` | 服务就绪检查 |
| `/health/dependencies/` | 依赖状态检查（需授权） |
| `/internal/metrics/` | Prometheus 指标（仅内部网络） |

## 质量验证

```powershell
# 后端
cd backend
py -3.10 manage.py test
py -3.10 manage.py makemigrations --check --dry-run

# 前端
cd ../frontend
npm run type-check
npm run build
```

检索与答案质量采用冻结测试集和逐题记录评估，覆盖 Recall@K、MRR、NDCG、引用准确性、忠实度、无答案识别及响应延迟。

## 文档

- [企业工作空间、RBAC 与审计](docs/stage-13-enterprise-workspace-rbac-audit.md)
- [账号安全、成员邀请与企业工作台](docs/stage-14-enterprise-workbench-invitations-security.md)
- [真实语料、结构化切片与可信评测](docs/stage-11-real-corpus-structured-chunking-evaluation.md)
- [生产部署与可观测性](docs/stage-15-production-observability.md)
- [生产运维手册](docs/stage-15-operations-runbook.md)
- [安全策略](SECURITY.md)

## 安全说明

- 不要提交 `.env`、数据库、上传文件、备份或任何真实密钥。
- 模型地址默认拒绝本机、私有网段、链路本地及云元数据地址。
- 浏览器认证使用 HttpOnly Session Cookie 与 CSRF 防护。
- 公开 API Key 仅存储哈希，模型密钥加密存储并始终脱敏返回。
- 生产环境应限制健康依赖和指标接口只允许内部网络访问。
