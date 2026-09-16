# 前端工程化、自动化验收与性能设计

## 1. 现状审计

本阶段基线提交为 `e36533b`，开发分支为 `feat/stage-17-frontend-engineering`。

### 1.1 自动化基线

- 后端 `py -3.10 manage.py test`：193 项测试通过，耗时 134.079 秒。
- `makemigrations --check --dry-run`：无模型变更。
- `manage.py check`：无问题。
- 前端 `npm run type-check`：通过。
- 本机 `npm run build`：受当前受限 Windows 执行环境影响，Node 无法 spawn esbuild 子进程，错误为 `spawn EPERM`。这不是 TypeScript 或 Vite 编译诊断；最终生产构建以 GitHub Actions Ubuntu Runner 为准。

### 1.2 页面与状态

`KnowledgeDetailView.vue` 当前 1913 行、79,433 字节，包含 64 个 `ref`、3 个 `reactive`、6 个 `computed` 和 65 个函数。单个页面同时负责：

1. 知识库上下文加载。
2. 文档上传、删除、重处理、异步任务轮询。
3. 切片列表、切片配置和预览。
4. 会话列表、历史消息分页、URL 恢复、改名和删除。
5. SSE 问答、Agent 轨迹和引用展示。
6. Chat/Embedding 模型绑定。
7. 混合检索设置、检索调试和 A/B 对比。
8. Agent 配置。
9. 全部抽屉和工作台模板。

页面已经用请求序列号保护部分历史消息、切片预览和检索调试请求，但存在以下缺口：

- `knowledgeId` 在 setup 时读取一次，复用组件切换路由参数时不会重新初始化。
- SSE 请求没有 `AbortSignal`，离开页面或开始新会话时只能忽略事件，不能释放连接。
- 知识库、文档、模型状态和会话列表加载没有统一的过期响应保护。
- 流式内容逐事件修改响应式对象并调用滚动，长回答可能产生过多渲染。
- 页面卸载时未递增历史和 Agent 请求序号，也未中断 SSE。

### 1.3 API 层

`frontend/src/api/index.ts` 当前 1416 行、45,043 字节，包含约 190 个导出的类型或函数。身份、租户、知识库、文档、会话、模型、检索、Agent、应用、仪表盘、任务和向量索引共享一个文件。

`api/client.ts` 已统一 Axios、CSRF、工作空间 Header 和 401 跳转，但错误对象只有文本与状态码，没有请求 ID、错误分类和取消语义。普通 Axios 请求与三个 Fetch SSE 实现散落在聚合 API 文件中。

### 1.4 构建与测试

- 路由已经全部使用动态 `import()`，本阶段只验证，不重复改造。
- `main.ts` 使用 `app.use(ElementPlus)` 并导入完整 CSS。
- 历史构建产物中 Element Plus JavaScript 为 786,432 字节（gzip 248,115），CSS 为 357,406 字节（gzip 47,356）。
- 实际入口脚本约 21 KB（gzip约 5.9 KB），Vue Vendor 约 42 KB（gzip约 16.5 KB）。
- 知识库详情页业务 Chunk 约 51.5 KB（gzip约 15 KB），Markdown Chunk 约 39.5 KB（gzip约 12 KB）。
- 当前没有 Vitest、Vue Test Utils 或 Playwright 配置与测试文件。

### 1.5 Markdown 安全

模型回答通过 `marked.parse()` 后直接传给 `v-html`，缺少 HTML 白名单过滤。恶意文档内容或模型输出可能生成事件属性、危险协议或不受控 HTML。

## 2. 范围与非范围

### 2.1 范围

- 按领域拆分前端 API 和类型，同时保留 `../api` 桶导出兼容层。
- 将知识库详情页拆成页面编排、领域 Composable 和可测试组件。
- 为普通请求和 SSE 建立明确的取消、过期响应和生命周期规则。
- 使用 `marked + DOMPurify` 安全渲染 Markdown。
- 建立 Vitest、Vue Test Utils、jsdom 和 Playwright。
- 在 CI 中执行前端单元/组件测试、构建和独立 E2E。
- 通过 Element Plus 自动按需导入降低首屏 Vendor 体积。

### 2.2 非范围

不增加新的后端业务、Agent 工具、MCP、SSO、SCIM、OCR、对象存储或向量数据库；不更改后端公开 API 契约，不重做视觉设计，不迁移真实数据。

## 3. 当前与目标职责

```text
当前
KnowledgeDetailView
 ├─ API 调用与状态
 ├─ 文档/任务/切片
 ├─ 会话/SSE/Agent轨迹
 ├─ 模型/检索/Agent配置
 └─ 所有模板

目标
KnowledgeDetailView（路由与编排）
 ├─ useKnowledgeContext
 ├─ useDocumentManagement ── DocumentPanel / Chunk drawers
 ├─ useConversationHistory ─ ConversationDrawer
 ├─ useStreamingChat ─────── ChatWorkbench
 ├─ useModelBinding ──────── ModelBindingDrawer
 ├─ useRetrievalWorkbench ── Retrieval drawers
 └─ useAgentSettings ─────── AgentSettingsDrawer
```

拆分使用显式 Props/Emits 和窄接口。不会创建一个包含全部状态的“万能 Context”，也不会把页面局部状态强行放入 Pinia。

## 4. 状态边界

- Pinia：登录用户、组织/工作空间、跨页面权限。
- 页面编排：当前 `knowledgeId`、知识库摘要、领域模块之间的刷新协调。
- Composable：领域请求、领域 loading、分页、请求序列和取消器。
- 组件：抽屉开关、表单草稿、展开项和视觉状态。
- API Key、CSRF Token 和流式正文不进入持久化 Store。

## 5. API 模块

`api/index.ts` 变成只做兼容导出的桶文件。目标模块为：

- `client.ts`：Axios、统一错误、CSRF、请求 ID。
- `sse.ts`：SSE 解码、Fetch 错误、取消语义。
- `auth.ts`：认证、会话和邀请。
- `organizations.ts`：组织、工作空间和审计。
- `knowledgeBases.ts`：知识库。
- `documents.ts`：文档、切片和处理任务。
- `conversations.ts`：会话、消息和知识库问答流。
- `models.ts`：模型配置和知识库绑定。
- `retrieval.ts`：检索、调试、Agent 配置和轨迹。
- `applications.ts`：应用、发布、公开访问和公开聊天流。
- `operations.ts`：仪表盘、任务中心和向量索引。
- `types.ts`：跨领域分页等极少量公共类型；领域类型优先跟随领域模块。

旧导入路径在本阶段继续可用，避免一次性修改所有页面产生无关回归。

## 6. TypeScript 策略

- 保持 `strict` 与 `noEmit`。
- API 字段沿用服务端 snake_case，避免隐式映射。
- 外部未知值先使用 `unknown` 和类型守卫。
- 不使用无必要的 `any` 和双重类型断言。
- SSE 事件由解析器返回稳定的 `{ event, data }`，业务层再校验事件载荷。

## 7. SSE 生命周期与竞态

```text
IDLE → CONNECTING → STREAMING → COMPLETED
                         ├────→ FAILED
                         └────→ CANCELLED
```

- API 接收可选 `AbortSignal` 并传给 Fetch。
- 页面离开、知识库切换、新建/切换会话时取消当前流。
- `AbortError` 映射为 `CANCELLED`，不显示服务器错误。
- 每次加载保存资源 ID 快照和请求序号，完成时同时校验两者。
- SSE 内容先进入缓冲区，按动画帧或短间隔批量提交，降低渲染频率。
- 成功才刷新会话摘要；失败与取消遵守后端消息持久化事实，不伪造成功消息。

## 8. Markdown 安全

```text
Markdown → marked → DOMPurify 严格白名单 → v-html
```

禁止脚本、iframe、事件属性、SVG/MathML 攻击面和危险协议。链接仅允许安全协议；外链补充 `target="_blank"` 与 `rel="noopener noreferrer"`。所有模型 Markdown 统一调用一个纯函数，不允许组件自行直接 `marked.parse()`。

## 9. 测试金字塔

### 9.1 单元测试

覆盖响应外壳、错误分类、请求 ID、SSE 解码/取消、请求序列、会话 URL、状态映射和 Markdown 清洗。

### 9.2 组件测试

优先覆盖拆出的文档状态、会话选择、聊天发送状态、引用和 Markdown 安全边界。断言用户行为，不依赖大面积快照。

### 9.3 E2E

Playwright 使用 `stage17_e2e_` 前缀账号与资源，使用本地降级回答，不配置真实模型密钥。场景覆盖认证、知识库、上传、问答、历史恢复、权限和应用入口；清理由专用脚本或后端测试管理命令限定前缀执行。

## 10. 性能预算

基线以历史 `dist/index.html` 实际引用资源为准。目标不是虚构固定百分比，而是：

- Element Plus 不再作为首屏 786 KB 的完整 JavaScript Chunk。
- 登录页不预加载知识库详情、Markdown 和低频工作台组件。
- 详情页按打开动作加载检索调试、切片设置等低频抽屉。
- CI 生成资源清单并阻止入口或主要 Vendor 超过审计后确定的预算。
- 优化报告同时保存 raw 与 gzip 数据；失败或无提升也如实记录。

## 11. CI 设计

```text
backend ─────────────────────┐
frontend(typecheck/test/build) ├─ production-containers
                              └─ e2e
```

E2E 在隔离服务和临时数据库中运行，失败上传截图、trace 和必要日志，始终清理服务。不会使用生产数据库或真实外部模型。

## 12. 垂直切片

1. 测试框架、Markdown 安全工具和行为特征测试。
2. API 与类型模块化，保持桶导出兼容。
3. 文档、任务、切片模块拆分。
4. 会话、聊天与 SSE 模块拆分及取消。
5. 模型、检索和 Agent 设置模块拆分。
6. Element Plus 按需导入、低频组件异步加载和体积比较。
7. Playwright、CI、真实浏览器验收和复查。

每个切片先运行类型检查和相关测试，再进入下一切片。

## 13. 测试矩阵

| 风险 | 自动化证据 |
| --- | --- |
| 错误响应泄漏或难定位 | client 单元测试，验证安全消息、状态与 request ID |
| SSE 跨会话写入 | AbortSignal、请求序列和会话切换测试 |
| XSS | script、事件属性、危险 URL、SVG 测试 |
| 文档操作回归 | DocumentPanel 组件测试与上传 E2E |
| 会话恢复回归 | URL 工具测试与刷新 E2E |
| 模型密钥进入状态 | 模型表单组件测试 |
| 路由/权限回归 | 路由与 Playwright 权限流程 |
| 构建体积反弹 | 构建报告与 CI 预算脚本 |
| 后端契约回归 | 193 项后端测试与前端类型检查 |

## 14. 设计自审

- 路由懒加载已经存在，因此不把既有能力包装成本阶段成果。
- 不引入 TanStack Query 或新的全局状态框架；现有规模用 Composable、AbortController 和请求序列足够。
- API 桶文件只承担兼容导出，不继续放实现。
- 页面拆分同时移动领域状态和行为，避免只拆模板。
- E2E 不依赖真实模型，避免费用、秘密和不稳定网络。
- Element Plus 按需导入需要以构建产物证明收益，不能只看配置。
- 本地 esbuild 限制不伪装为构建通过，使用 CI Runner 补验。

## 15. 实际实施与验证记录

### 15.1 已实施结构

- `KnowledgeDetailView.vue` 从 1913 行缩减为 104 行，只保留路由读取、模块组合、初始化、跨模块刷新和卸载清理。
- 新增 7 个领域组件：文档面板、文档抽屉、聊天工作台、消息列表、会话抽屉、模型绑定抽屉、Agent/检索抽屉。
- 新增 6 个 Composable：知识库上下文、文档管理、会话与流式聊天、模型绑定、检索工作台、Agent 设置；页面状态没有迁入 Pinia。
- `api/index.ts` 从 1416 行缩减为 11 行兼容导出；实现拆到 10 个领域模块和独立 SSE 解析器。
- SSE 增加 `AbortSignal`、六态生命周期、请求序列与 32 ms 内容缓冲。切换会话、新建会话及卸载都会取消旧连接。
- Markdown 统一经过 `marked` 和 DOMPurify，禁止脚本、事件属性、iframe、SVG/MathML 和危险 URL，外链补齐安全属性。
- Element Plus 从全量插件和全量 CSS 改为组件/指令按需解析；消息提示样式单独显式引入。
- 低频抽屉使用 `defineAsyncComponent`，现有路由动态导入保持不变。

### 15.2 测试与 CI

- 建立 Vitest、Vue Test Utils 和 jsdom，覆盖统一错误、请求 ID、SSE、请求守卫、URL 会话状态、状态映射、Markdown XSS、文档面板、会话抽屉和消息引用。
- 建立 Playwright 隔离流程，使用 `stage17_e2e_` 前缀、独立 SQLite 与 MEDIA_ROOT，不接入真实模型和密钥；覆盖注册、知识库、上传、切片、问答、引用、刷新恢复、第二会话、模型密钥空值、应用和注销。
- CI 前端顺序调整为 `npm ci → type-check → test → build:budget`；E2E 独立启动前后端，失败上传 trace/截图，最后清理数据库和媒体目录。
- 构建预算：总 JS 不超过 1.5 MB、最大 Chunk 不超过 650 KB、入口 Chunk 不超过 100 KB。预算以审计基线留出兼容余量，后续可根据真实 CI 产物逐步收紧。

### 15.3 已执行验证

| 验证 | 结果 |
| --- | --- |
| `py -3.10 manage.py test` | 193 项通过，134.643 秒 |
| `makemigrations --check --dry-run` | 无模型变更 |
| `manage.py check` | 无问题 |
| `npm run type-check` | 通过 |
| `docker compose ... config --quiet` | 使用 `.env.production.example` 通过 |
| `npm run test` | 本机未执行：Node 启动 esbuild 时被当前沙箱以 `spawn EPERM` 拒绝；由 CI 补验 |
| `npm run build` | 同上，由 CI Linux Runner 补验 |
| 生产镜像构建 | Docker Engine 已运行，但当前进程无权访问 `dockerDesktopLinuxEngine` 命名管道；由 CI 补验 |

### 15.4 性能证据

优化前可复现基线：Element Plus JS 786,432 B / gzip 248,115 B，CSS 357,406 B / gzip 47,356 B，入口约 21 KB / gzip 5.9 KB，知识库详情约 51.5 KB / gzip 15 KB。

优化后 raw/gzip 数值必须取自 GitHub Actions 的真实 Vite 输出和预算脚本，CI 未完成前不填写估算值。长回答由“每个 SSE 事件立即更新并滚动”改为最长 32 ms 合并一次响应式提交。

## 16. 已知限制

- 当前 Windows 沙箱阻止 Node spawn esbuild，生产构建需要 GitHub Actions 或用户本机普通终端补验。
- Playwright 浏览器下载和首次运行可能依赖网络；无法本地执行时必须由 CI 补验。
- 本阶段不改变后端流式中断后的消息持久化规则，只保证前端不把取消误报为成功。
- E2E 的权限隔离仍主要由既有后端测试证明；浏览器流程使用新注册账号验证其所属组织边界，不创建第二个跨组织攻击账号。
