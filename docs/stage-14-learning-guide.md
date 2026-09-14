# 第十四阶段学习指南

## 1. Cookie Session 与 Token

旧登录在 `api.views.LoginView` 创建永久DRF Token，客户端每次把它放进Authorization。新登录在 `api.auth_views.AuthLoginView` 调用Django `login()`，浏览器只得到HttpOnly `sessionid`；后端通过Session表找到用户。前端不可读取HttpOnly Cookie，XSS更难直接偷走凭据。TokenAuthentication仍保留给兼容调用。

## 2. 为什么不把Token放localStorage

localStorage可被页面中的JavaScript读取。前端 `stores/auth.ts` 现在启动即清理历史token，只在内存保存CurrentUser；刷新状态来自 `/api/auth/me/`。

## 3. CSRF如何工作

`/api/auth/csrf/`设置可读的`csrftoken`。Axios在写请求中把它复制到`X-CSRFToken`，Django比较Header和Cookie；攻击网站即使能让浏览器携带Session Cookie，也拿不到正确Token。后台SSE的fetch同样显式发送CSRF。

## 4. Session Fixation

攻击者若预先指定Session ID，登录后继续沿用就可能劫持会话。Django `login()`会轮换Session Key；阶段测试验证登录前后发生轮换。

## 5. User 与 AccountProfile

项目已有Django User，直接替换自定义User会扩大迁移风险。因此新增一对一AccountProfile承载规范化邮箱和验证时间，User继续负责用户名、密码和Django权限基础。

## 6. 邮箱规范化

`normalize_email()`执行trim+lower，AccountProfile对非空邮箱施加唯一约束。这样`A@EXAMPLE.COM`和`a@example.com`不能注册为两个身份。历史迁移只回填格式合法且唯一的邮箱，不猜测冲突归属。

## 7. 邀请Token只保存摘要

原始Token是登录能力，泄漏数据库不应直接变成可用邀请。`services/invitations.py`用`secrets.token_urlsafe(32)`生成随机值，只保存SHA-256摘要；收到URL Token后重新计算摘要查询。

## 8. 为什么接受邀请需要事务

`accept_invitation()`在事务内锁定邀请，依次校验状态、过期时间和邮箱，再创建组织/工作空间Membership并标记ACCEPTED。任一步失败整体回滚，避免“邀请已使用但成员没创建”的半状态。

## 9. 防止重复和并发接受

状态只能从PENDING进入ACCEPTED，数据库事务使用`select_for_update`；相同组织+邮箱同时只能有一个PENDING邀请。SQLite仅验证语义，真正的行锁并发需在PostgreSQL复验。

## 10. 新用户如何加入组织

`register-and-accept`先从Token确定不可修改的邀请邮箱，再用RegisterSerializer创建用户；随后调用同一accept Service建立两层Membership，最后创建浏览器Session。注册和接受处在同一外层事务。

## 11. 密码重置为何不提示邮箱不存在

若存在和不存在返回不同文案，攻击者可枚举企业账号。`PasswordResetRequestView`始终返回同一句话；只有后台内部决定是否通过Django邮件抽象发送重置链接。

## 12. 撤销旧登录会话

AccountSession只保存Session Key摘要用于展示定位。修改密码保留当前Session并删除其他Django Session；密码重置删除该用户全部Session。Service会解码Session归属，因此也能覆盖尚未写入AccountSession的旧会话。

## 13. 工作台如何聚合

`DashboardOverviewView`从KnowledgeBase、Document、DocumentProcessingTask、ModelConfig、Application和ApplicationAccessLog聚合数量、分子/分母、平均延迟和P95。P95取排序后`ceil(0.95*n)-1`位置，不凭空写百分比。

## 14. 如何防止跨工作空间统计

每个查询都先由`resolve_workspace_access()`解析`X-Workspace-ID`，然后明确过滤`workspace`或沿外键过滤`knowledge_base__workspace`、`application__workspace`。前端切换工作空间会重新加载页面数据。

## 15. 全局任务中心为何不是新状态机

`GlobalTask*View`只把当前空间多个知识库的DocumentProcessingTask合并查询；重试和取消继续调用原`retry_processing_task()`与`request_task_cancel()`。这样单库页面和全局页面不会出现两套状态规则。

## 16. Vue如何恢复登录

路由守卫首次导航时调用Auth Store的`initialize()`；它请求`/auth/me/`，成功写入内存用户，失败进入登录页。登录成功后再请求me，不依赖浏览器可读凭据。

## 17. Axios如何带Cookie和CSRF

`api/client.ts`设置`withCredentials`、`xsrfCookieName`和`xsrfHeaderName`。普通请求由Axios完成；原生fetch实现的SSE设置`credentials:'include'`并读取csrftoken填入Header。

## 18. 测试如何证明Token没有泄漏

测试断言数据库`token_digest`不等于原始Token、预览不含完整邮箱、重复使用返回410；日志过滤器把邀请URL中的长Token替换为`<redacted>`。前端代码搜索只允许`removeItem('token')`，不再有读取和写入。

## 19. CI如何保护main

`.github/workflows/ci.yml`在push和PR运行Django全测、迁移漂移检查、system check、npm clean install、TypeScript检查和生产构建。仓库开启分支保护后，可把CI成功设为合并条件。

## 20. 企业级与普通CRUD的区别

CRUD只证明“能增删改查”；企业级系统还必须明确身份生命周期、租户边界、权限、审计、幂等与并发、一致性、错误降级、可观测指标、生产配置和可复现验证。本阶段把“登录→邀请→入组→看运行状态→处理异常任务”串成可审计闭环。
