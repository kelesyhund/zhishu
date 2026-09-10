# 第十阶段学习交付：AI应用发布与第三方接入

这份文档面向刚开始学习全栈开发的开发者。它不只列出代码文件，而是解释一次用户操作如何从Vue页面进入Django，再经过Service、Model、数据库和SSE返回浏览器。

对应设计与验收记录见 `docs/stage-10-application-publishing.md`。

## 1. 这一阶段解决了什么问题

前九阶段主要围绕“登录后台后使用某一个知识库”。第十阶段在知识库之上增加了可发布的AI应用：

1. 一个应用可以组合一个到五个知识库。
2. 管理员可以编辑草稿并预览。
3. 发布后产生不可变版本，草稿修改不会立即影响公开用户。
4. 用户可以通过公开页面、iframe或第三方API使用应用。
5. 公开用户不能获得后台登录Token，也不能访问其他访客的会话。

因此，知识库和应用不是同一个概念：

```text
知识库：文档 + Paragraph + Embedding + 单库检索参数
应用：知识库组合 + Chat模型 + Prompt + Agent + 展示配置 + 发布版本 + 访问方式
```

## 2. 主要数据模型

代码位置：`backend/api/models.py`。

### Application

保存正在编辑的草稿，例如名称、系统Prompt、欢迎语、Chat模型、Agent设置、全局Top-K和上下文预算。

`status` 有三种状态：

- `DRAFT`：草稿，还没有正式对外服务。
- `PUBLISHED`：存在正在使用的正式版本。
- `DISABLED`：已停用，历史数据保留，但公开页面和应用API不可用。

### ApplicationKnowledgeBase

这是Application和KnowledgeBase之间的中间表。一个应用可以绑定多个知识库，一个知识库也可以被多个应用复用。

`position` 表示顺序，`weight` 表示跨库RRF融合权重，`enabled` 表示当前是否启用。

### ApplicationVersion

每次发布时生成一条版本记录。`config_snapshot` 是当时应用配置的JSON快照，其中只保存配置ID、revision、Prompt和知识库ID等非敏感信息，不保存API Key和文档正文。

### ApplicationCredential

保存第三方API访问凭证。数据库只保存：

- 用于快速查询的随机前缀；
- 完整Token的SHA-256摘要；
- 最后四位；
- 是否启用和过期时间。

完整API Key只在创建响应中出现一次。

### ApplicationPublicAccess

保存公开链接Token的摘要、启用状态和iframe允许嵌入的Origin。完整公开Token同样只在首次创建或轮换时返回一次。

### Conversation与Message

Conversation经过扩展后既能表示原知识库后台会话，也能表示应用预览、公开网页、iframe和第三方API会话。

一个Conversation拥有多条Message，这仍然是一对多关系。公开会话通过`visitor_id_hash`隔离，而不是伪造一个后台User。

## 3. 从创建应用到数据库的完整链路

用户在 `ApplicationListView.vue` 点击“创建应用”后：

```text
Vue按钮事件 create()
→ frontend/src/api/index.ts 的 createApplication()
→ POST /api/applications/
→ backend/api/urls.py 路由匹配
→ ApplicationListView.post()
→ ApplicationWriteSerializer校验
→ Application.objects.create()
→ 返回统一JSON响应
→ Vue Router进入应用编辑页
```

Serializer负责检查名称是否为空、同一用户是否重名、Chat配置是否属于当前用户、数值范围是否正确。View不需要重新手写这些字段校验。

## 4. 为什么应用草稿和正式版本必须分开

如果公开用户每次都直接读取Application，管理员在输入Prompt到一半时，线上回答就可能立即改变。发布快照解决了这个问题。

发布调用链：

```text
发布按钮
→ POST /api/applications/{id}/publish/
→ ApplicationPublishView
→ publish_application()
→ transaction.atomic()
→ select_for_update()锁定应用
→ 校验知识库、文档、模型和工具
→ 创建ApplicationVersion快照
→ 更新current_published_version
```

`transaction.atomic()`保证“创建版本”和“切换当前版本”同时成功或同时回滚，避免数据库中出现半发布状态。

回滚不删除任何版本，只把`current_published_version`重新指向旧版本。草稿也不会被旧版本覆盖。

## 5. 多知识库检索为什么使用RRF

一个应用的不同知识库可能使用不同Embedding模型。不同模型的相似度分数不在同一个尺度上，不能简单把0.92和0.86直接比较。

系统先让每个知识库独立执行原有检索，再根据名次计算：

```text
RRF分数 = 知识库权重 / (60 + 当前切片在该知识库中的名次)
```

然后合并所有候选，统一执行应用级Top-K和上下文字符预算。这样比较的是“各自在本知识库中的排名”，不会直接混合不同Embedding模型的原始向量分数。

代码位置：`backend/api/services/application_retrieval.py`。

## 6. 公开页面的完整请求链路

公开页面路由是 `/share/:token`，不要求后台登录Token。

首次打开时：

```text
PublicChatView加载
→ GET profile验证公开Token和正式版本
→ POST visitor生成短期访客签名
→ visitor_token只保存在sessionStorage
```

发送问题时：

```text
streamPublicApplicationChat()
→ X-Visitor-Token请求头
→ PublicApplicationChatView
→ 验证公开Token、应用状态和访客签名
→ 检查IP级和应用级限流
→ 验证conversation_id属于当前访客
→ 创建用户Message
→ 跨库检索或运行Agent
→ SSE逐块返回content
→ 成功后保存完整AI Message和references
```

刷新页面时，前端从sessionStorage恢复访客签名和conversationId，再调用分页消息接口。后端仍会重新验证会话是否属于该访客，所以只修改浏览器中的conversationId不能读取别人的消息。

## 7. SSE为什么不是WebSocket

SSE适合“浏览器发一个问题，服务器持续向浏览器推送回答”的单向流式场景。它使用普通HTTP响应，前端读取`ReadableStream`并解析：

- `meta`：会话ID和版本；
- `content`：回答片段；
- `references`：最终引用；
- `done`：成功结束；
- `error`：安全错误信息；
- Agent模式还包含工具开始、结果和结束等审计事件。

只有AI回答完整成功后才保存assistant Message。模型中途失败时保留用户问题，但不会产生空AI消息。

## 8. API Key为什么保存摘要而不是加密密文

模型供应商的API Key以后还要由服务器解密并调用外部模型，所以第五阶段使用Fernet可逆加密。

应用访问Key只用于判断“调用者提交的Key是否正确”，服务器不需要恢复原文。因此更适合保存不可逆SHA-256摘要：

```text
客户端提交完整Key
→ 后端计算SHA-256
→ 与数据库摘要做常量时间比较
→ 相同则认证成功
```

数据库泄漏时，攻击者不能直接复制摘要作为应用Key使用。前端也不会把完整Key写入localStorage、URL或Pinia持久化状态。

代码位置：`backend/api/services/application_credentials.py`。

## 9. 公开Token、API Key和访客Token的区别

| 凭证 | 使用者 | 作用 | 浏览器保存位置 | 数据库存储 |
|---|---|---|---|---|
| 后台Token | 登录管理员 | 管理知识库和应用 | 原认证Store | Django Token表 |
| 公开Token | 分享链接 | 找到并访问正式应用 | URL | SHA-256摘要 |
| 应用API Key | 第三方服务器 | 调用Chat Completions接口 | 不应持久化在网页 | SHA-256摘要 |
| visitor_token | 匿名访客 | 隔离公开会话 | sessionStorage | 只保存随机nonce摘要 |

这些Token不能互相替代。

## 10. Agent应用如何复用原执行器

应用没有重新实现第二套Agent循环，而是继续调用`stream_agent_run()`：

1. Conversation指向Application和创建时使用的ApplicationVersion。
2. AgentExecutor从版本快照解析Chat配置、Prompt、步骤限制和工具列表。
3. 当前只允许一个知识库，避免模型在多库之间产生模糊工具作用域。
4. 公开/API运行时再次检查工具安全元数据。
5. 只有同时满足`allow_public=True`、`side_effect_level=NONE`、`requires_owner=False`的工具才能公开执行。
6. 每次执行继续写入AgentRun和ToolExecution。

这属于“发布时校验一次，运行时再次校验”的纵深防御。

## 11. iframe白名单如何工作

第三方页面不直接嵌入后台管理页面，而是嵌入：

```text
/api/public/applications/{public_token}/embed/
```

后端根据`allowed_frame_origins`生成响应头：

```text
Content-Security-Policy: frame-ancestors https://portal.example.com
```

浏览器根据这个响应头判断哪些父页面可以嵌入应用。只在前端写一个“允许域名”变量并不能提供这种保护，必须由被嵌入文档的HTTP响应头限制。

## 12. 为什么还要做日志脱敏

公开Token位于分享URL中，开发服务器或反向代理默认可能记录完整请求路径。本阶段新增`ApplicationSecretFilter`，把Django日志中的完整公开Token和应用Key替换为：

```text
kc_pub_<redacted>
kc_app_<redacted>
```

生产部署时Nginx、Traefik或云负载均衡器也必须配置同等脱敏。Django过滤器无法自动修改其上游代理已经写出的日志。

## 13. 删除规则

删除已发布应用前必须先停用。删除Application会级联清理：

- 版本；
- 知识库绑定关系；
- 应用访问凭证；
- 公开访问配置；
- 应用会话和消息；
- 应用访问日志。

KnowledgeBase、Document、Paragraph和ModelConfig不会被删除。反过来，知识库只要仍被应用绑定，就会被`PROTECT`阻止删除，并返回409提示先解除绑定。

## 14. 前端页面之间如何分工

- `ApplicationListView.vue`：搜索、分页、创建和删除应用。
- `ApplicationEditView.vue`：修改草稿、绑定知识库、设置Prompt/Agent并预览。
- `ApplicationOverviewView.vue`：发布、版本回滚、公开访问、iframe白名单、API Key和访问日志。
- `PublicChatView.vue`：面向匿名访客的精简聊天页面，不包含后台管理能力。
- `frontend/src/api/index.ts`：封装HTTP请求和SSE解析，让页面组件主要维护交互状态。

## 15. 自动化测试证明了什么

`backend/api/test_applications.py`覆盖了：

- 用户只能管理自己的应用；
- 不能绑定其他用户的知识库；
- 发布快照不会被后续草稿修改；
- 回滚只切换版本；
- 删除应用不会删除知识库和文档；
- API Key和公开Token不以明文保存；
- Token轮换后旧Token立即失效；
- 不同访客不能读取彼此会话；
- 一个应用的API Key不能调用另一个应用；
- iframe响应包含正确CSP；
- Web日志不会留下完整Token；
- 多知识库按权重RRF融合；
- 限流达到阈值后返回429和Retry-After；
- 应用Agent复用执行器并支持SSE和普通JSON响应。

最终全量后端95项测试通过，前端TypeScript检查通过。

## 16. 建议阅读顺序

1. 先看 `frontend/src/views/ApplicationListView.vue` 的创建按钮。
2. 跟到 `frontend/src/api/index.ts` 的`createApplication()`。
3. 查看 `backend/api/urls.py` 对应路由。
4. 阅读 `ApplicationListView.post()`和`ApplicationWriteSerializer`。
5. 阅读Application及其关联模型。
6. 阅读`application_publishing.py`理解事务和快照。
7. 阅读`application_runtime.py`理解草稿/正式版本解析。
8. 阅读`application_retrieval.py`理解跨库RRF。
9. 阅读公开Chat View和`PublicChatView.vue`理解访客Token、SSE与会话恢复。
10. 最后阅读AgentExecutor和阶段测试，理解运行时安全校验怎样被测试证明。

## 17. 当前限制

- 多知识库仍按顺序检索，未并行执行。
- 向量仍保存在JSON中并由Python计算。
- 应用Agent当前只支持一个知识库。
- 第三方Chat Completions只实现项目文档承诺的常用子集。
- 生产反向代理仍需单独配置Token日志脱敏、可信代理IP和集中监控。
- 当前本机外部模型连接失败，只验证了安全错误提示；真实成功回答需要模型地址和网络可用后补验。
