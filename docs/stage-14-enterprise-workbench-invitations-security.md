# 第十四阶段：企业工作台、成员邀请与账号安全

## 1. 现状审计与基线

- 2026-09-11 开发前后端 128 项测试通过；`api` 迁移 0001～0012 已应用，`makemigrations --check --dry-run` 无漂移。
- 前端 `npm run type-check` 通过；标准 Vite 构建在当前受限 Windows 环境中仍因 `esbuild spawn EPERM` 失败，这是开发前基线限制。
- DRF 默认只有 `TokenAuthentication`；登录返回永久 Token，Vue 把 Token 和用户名写入 `localStorage`，Axios 与两处后台 SSE 请求再读取 Token。XSS 一旦执行即可窃取长期凭据。
- 注册邮箱选填、未建立大小写无关唯一身份；组织新增成员要求账号已存在。密码虽然调用 `validate_password`，但设置中未启用任何标准验证器。
- `Organization/Workspace/Membership/AuditEvent` 已形成固定角色和 capability 边界；`DocumentProcessingTask` 可沿 `Task → Document → KnowledgeBase → Workspace` 证明归属；`ApplicationAccessLog` 可提供真实调用量、成功率与延迟。
- `/health/` 仅提供存活信号；`DEBUG` 默认开启、`ALLOWED_HOSTS=["*"]`、开发密钥可进入非开发环境，部署检查有 7 项警告。

## 2. 范围与非范围

范围：Cookie Session + CSRF、账号邮箱身份、密码修改/重置、登录会话、组织邀请、真实工作台、跨知识库任务中心、审计、CI与生产设置整理。非范围：SSO/LDAP/SCIM/MFA、自定义角色、资源ACL、计费、MCP、新Agent工具、OCR、对象存储和UI框架替换。

## 3. 数据模型与 Migration

- `AccountProfile`：User 一对一；`normalized_email` 可空但非空值唯一；记录验证时间。迁移仅回填格式合法且大小写归一后唯一的历史邮箱，空/非法/冲突数据不删除并保留为空。
- `AccountSession`：保存 Django Session Key 的 SHA-256 摘要、受限 User-Agent、IP HMAC、最近活跃与撤销时间；不把原始 Session Key 返回前端。
- `OrganizationInvitation`：组织、规范化邮箱、非 OWNER 角色、SHA-256 Token 摘要、状态、过期时间、邀请/接受人及时间。原始 256 位随机 Token 只在创建/重发响应与邮件中出现一次。
- `InvitationWorkspaceGrant`：邀请与同组织工作空间的授权角色；组合唯一。

## 4. Session、CSRF 与 Token兼容

知枢 SPA 使用 Django Session Cookie；登录调用 `django.contrib.auth.login` 自动轮换 Session ID。Axios `withCredentials=true`，从 `csrftoken` Cookie 读取 CSRF 并发送 `X-CSRFToken`。启动先请求 `/api/auth/csrf/`，再用 `/api/auth/me/`恢复账号。旧 `/api/login/` 与 DRF TokenAuthentication 保留给兼容测试和内部调用，公开应用 Token/应用凭证不变。

## 5. 邮箱、密码和会话规则

新注册必须提供唯一规范化邮箱。历史空邮箱账号可继续登录并收到补全提示。修改密码使用 Django 密码校验，成功后保留当前会话并撤销其他会话；密码重置申请始终返回同一提示，使用 Django 有时效且不可重放的 uid/token，成功后撤销全部旧会话。开发使用 console 邮件，测试使用 locmem，未经授权不发真实邮件。

## 6. 邀请状态机与安全

`PENDING → ACCEPTED | REVOKED | EXPIRED`。创建/重发撤销同邮箱旧有效邀请；禁止授予 OWNER；工作空间必须属于邀请组织。预览仅返回组织名、脱敏邮箱、状态和过期时间。接受时锁定邀请行，在同一事务内校验摘要、状态、时效、邮箱和角色，然后幂等创建组织及工作空间 Membership。数据库、审计和列表接口均不保存/返回原始 Token；接受成功后前端立即替换 URL。

## 7. API 契约

- 认证：`GET csrf/me/sessions`，`POST login/logout/register/change-password/password-reset/*`，`DELETE sessions/{public_id}`，`POST sessions/logout-others`，统一位于 `/api/auth/`。
- 邀请：组织内列表、创建、重发、撤销；公开 Token 预览、接受、注册并接受。
- 工作台：`GET /api/dashboard/overview|activity|attention-items/`，范围白名单 7d/30d。
- 任务中心：`GET /api/processing-tasks/`、详情、重试、取消。归属只通过外键链判断。
- JSON 延续 `{code,message,data}`；敏感资源不存在、越权和父子不匹配返回安全的 404/403。

## 8. 工作台指标口径

全部按当前工作空间过滤。调用成功率=`SUCCESS/全部已结束访问日志`；平均/P95总响应时间只统计已结束日志，P95采用排序后 ceil(0.95*n)-1；无答案以安全错误码 `NO_ANSWER` 计数；文档处理成功率=`SUCCESS/(SUCCESS+FAILURE)`。响应同时返回分子、分母与百分比；无数据为 0。待办来自失败/停滞任务、失效向量、失败/未测试模型、邀请到期和邮箱未验证。活动复用 AuditEvent，缺少 `audit.read` 时返回空活动而非泄漏。

## 9. 前端信息架构与状态

默认路由改为 `/dashboard`；新增工作台、任务中心、账号安全、邀请接受页，并把成员邀请嵌入组织治理页。Auth Store只保存内存用户和初始化状态，启动清除历史 Token。工作空间 ID仍可作为非敏感偏好存储。切换工作空间整页重新加载，避免旧请求覆盖新租户数据。

## 10. 权限与审计

邀请要求 `member.manage` 且验证组织关系；任务读要求 `knowledge.read`，重试/取消要求 `document.process`；工作台统计要求当前空间可见性，详细活动额外要求 `audit.read`。登录成功/失败、退出、密码修改/重置、邀请创建/重发/撤销/接受、会话撤销均记录安全原因码，不记录密码、Cookie、Token、邮箱明文或完整UA/IP。

## 11. 威胁模型与设计自审

HttpOnly Cookie缓解XSS直接读取凭据，CSRF Cookie+Header与SameSite阻止跨站写请求；登录轮换避免 fixation；邀请摘要、单次事务锁和邮箱匹配阻止泄漏、重放与角色提升；登录限流按用户名摘要与IP摘要双维度；所有聚合及任务沿 workspace 外键过滤。SQLite不能真实验证行锁并发，因此并发邀请接受仍须在PostgreSQL复验。第一版限流使用Django缓存原子 `add/incr`，多实例生产须配置共享Redis缓存。

## 12. 测试矩阵与清理

覆盖 Session/CSRF/Token兼容、密码强度和限流、邮箱规范化、密码重置枚举防护、邀请摘要/预览/过期/撤销/重放/角色/跨组织、Membership、会话撤销、工作空间指标隔离、任务权限与原阶段回归。测试使用临时数据库、locmem邮件和 `stage14` 标识；浏览器/HTTP验收后只清理精确匹配的临时数据。

## 13. 实际实施与验证记录

- 数据与安全：生成并应用 Migration `0013_accountprofile_accountsession_organizationinvitation_and_more.py`；历史邮箱只回填合法且唯一的数据。新增摘要化 AccountSession、邀请状态机、单次 Token、邮箱验证和 Django 密码重置。
- 认证：新增 `/api/auth/*` Session/CSRF 接口；SPA启动清理历史Token，普通Axios与两条后台SSE均使用Cookie/CSRF。旧TokenAuthentication及公开应用自有凭证保持兼容。失效Session统一返回401。
- 产品功能：新增真实企业工作台、7/30天指标与趋势、待办、审计活动、全局任务中心、账号安全页、邀请管理/接受页和邮件验证/密码重置页；登录后默认进入工作台。
- 生产交付：增加 GitHub Actions 双任务CI、`SECURITY.md`、环境变量说明；生产模式要求独立SECRET_KEY与ALLOWED_HOSTS，并启用Secure Cookie、HTTPS重定向与HSTS。
- 自动化：第十四阶段11项安全/功能测试通过，最终后端139/139项全量回归通过。测试覆盖CSRF、Session恢复、Token兼容、双维度限流、密码重置枚举防护、邮箱验证、其他Session撤销、邀请摘要/脱敏/重放/角色和工作空间统计隔离。
- 真实HTTP：在本地数据库应用0013后，使用demo会话完成CSRF→登录→me→上下文→工作台→待办→任务中心→会话列表，全部返回200；临时邀请创建后预览仅显示`st***@example.com`，撤销后返回410，随后精确清理1条临时邀请。
- 构建：`npm run type-check`通过。标准命令仍会被当前Codex Windows沙箱禁止Node创建esbuild子进程；使用同版本`esbuild-wasm 0.28.2`作为一次性、非入库的构建期执行替代后，Vite真实完成1725个模块转换并生成`dist`。随后已撤回临时替代，项目依赖与原生构建配置保持不变。
- 前端生产优化：页面路由改为动态导入，稳定依赖按`vue-vendor`、`element-plus`、`markdown`和通用`vendor`拆包；入口业务脚本由原先约1266 KB降至约21 KB，各页面形成独立懒加载资源。Element Plus原始包仍有约786 KB的非阻断体积警告，gzip后约249 KB。
- 生产检查：用临时生产环境变量运行`manage.py check --deploy`为0警告；开发默认运行仍有HTTPS相关预期差异。
- 静态产物：使用本地静态服务器加载生产`dist`，`index.html`以及入口JS、Vue vendor、Element Plus vendor和CSS资源均返回200。真实HTTP后端冒烟已通过；本轮后端未启动，因此没有将生产前端与后端的端到端点击描述为通过。

## 14. 已知限制

- 邮箱验证发送与企业SMTP投递不在未授权本地验收中；本阶段具备验证状态与安全邮件抽象。
- 登录限流依赖缓存后端；本地内存缓存只覆盖单进程。
- 当前Codex Windows沙箱仍会阻止标准Node进程直接创建esbuild子进程；普通本机终端和CI继续使用标准`npm run build`，不需要保留本轮一次性WASM替代。
- Element Plus目前保留完整主题CSS，后续可结合自动按需组件导入进一步降低UI依赖体积；当前警告不影响构建和运行。
- SQLite可验证事务语义，但邀请并发接受的行锁效果仍须在PostgreSQL部署中复验。
