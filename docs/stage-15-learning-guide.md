# 第十五阶段学习指南：从“能运行”到“能运维”

## 1. 为什么 `runserver` 不适合生产

Django `runserver`用于开发热更新，不负责稳定进程管理、可控并发、优雅停机和生产超时。本项目的生产镜像用Gunicorn管理进程和线程，Django仍只负责Web框架逻辑。

## 2. Nginx、Gunicorn和Django分别做什么

Nginx接浏览器连接、发静态文件和反向代理；Gunicorn管理执行Django的Worker/线程；Django完成认证、权限、Serializer、数据库和业务Service。三层职责不同。

## 3. 一次请求怎样到达Django

浏览器请求 `127.0.0.1:18080/api/...`，Compose把端口交给Nginx。Nginx传递Host、协议、客户端链和Request ID到`backend:8000`，Gunicorn选择线程执行Django middleware、URL和View，再原路返回。

## 4. `dist`是什么

Vue `.vue/.ts`不是浏览器最终加载形式。`npm run build`执行类型检查和Vite打包，输出带内容哈希的HTML、JS、CSS到`dist`。生产镜像只复制dist，不启动Vite开发服务器。

## 5. 什么是反向代理

客户端只认识Nginx，Nginx代表客户端访问内部Backend，所以叫反向代理。它隐藏内部拓扑，并统一处理TLS、缓存、大小限制和超时。

## 6. SSE为什么关闭缓冲

SSE依赖服务端每生成一段就flush。Nginx若先攒满缓冲区，浏览器会在最后一次性看到答案。`proxy_buffering off`和`X-Accel-Buffering:no`让chunk及时前送。

## 7. Docker镜像和容器

镜像是只读模板；容器是镜像的一次运行实例。Dockerfile定义镜像，Compose定义多个容器的网络、环境、依赖、健康与Volume。

## 8. Compose怎样连接服务

同一Compose网络里，服务名就是DNS名：Django用`postgres:5432`、`redis:6379`，Nginx用`backend:8000`。只有明确`ports`才暴露给宿主机。

## 9. PostgreSQL和Redis为何不能互换

PostgreSQL保存用户、知识库、消息和任务审计事实，强调事务与持久一致性。Redis保存队列、短期结果、锁、限流和心跳，强调低延迟。Redis丢失不能让数据库任务历史一起消失。

## 10. Broker和Worker

Backend把文档任务消息写到Redis Broker并立即返回202；Celery Worker取消息，调用`document_tasks.execute_document_processing_task()`，再进入`document_processor.process_document()`。202只表示提交，不代表处理成功。

## 11. Beat为什么只能有一个调度源

Beat按周期发送维护任务。多个Beat会重复调度。虽然本项目的收敛器靠数据库条件保持幂等，生产仍只启动一个Beat，减少无意义负载和竞态。

## 12. live和ready

`/health/live/`只问“进程还能回答吗”，依赖坏了也不触发重启风暴；`/health/ready/`问“现在可以接业务流量吗”，会检查PostgreSQL、Redis和生产配置。

## 13. 日志、指标和Trace

日志记录离散事件和诊断上下文；指标把大量事件聚合成时间序列；Trace把一次请求跨HTTP、检索、模型和Celery串成树。三者互补，不互相替代。

## 14. request_id和trace_id

Request ID对应一次HTTP入口，便于用户报错和Nginx/Django关联。Trace ID由OpenTelemetry生成，同一业务因果链的多个Span共享它，可跨异步任务。

## 15. Trace如何跨HTTP和Celery

`ObservabilityMiddleware`创建HTTP Span；`dispatch_processing_task()`把W3C trace headers注入Celery消息；`process_document_task`提取上下文并创建子Span。消息里不传Prompt、正文或Key。

## 16. Counter、Gauge和Histogram

Counter只递增，适合请求/失败总数；Gauge可增减，适合在线Worker、连接和队列深度；Histogram把延迟放入桶，可计算P50/P95/P99。

## 17. P50、P95和P99

排序后P95表示95%的样本不超过该值，尾部5%更慢。它比平均值更能暴露少量严重慢请求。必须同时给样本数、窗口和排除规则。

## 18. 高基数标签

若把request_id、用户、文档ID作为Prometheus标签，每个值会新建时间序列，内存和查询成本爆炸。本项目只允许route模板、状态类、任务类型等有限枚举；具体ID只进日志/Trace。

## 19. Prometheus、Grafana、Alertmanager

Prometheus定时拉取并存储指标；Grafana查询Prometheus并画Dashboard；Alertmanager负责告警分组和发送。本阶段实现Prometheus规则，但不接短信/电话渠道。

## 20. SLI、SLO、SLA和错误预算

SLI是实际测量值，SLO是内部目标，SLA是对外合同，错误预算是`1-SLO`允许的失败空间。设计中的99.9%是初始SLO，不是未实测的成果。

## 21. Worker宕机会怎样

消息可能仍在Broker、已取走但未ack，或任务数据库停在PROCESSING。`acks_late`让Worker异常后Broker可重投；`updated_at`和Beat收敛极端僵尸状态。重复执行仍要靠事务安全替换保持幂等。

## 22. 重试为什么必须幂等

网络超时不能证明服务端完全没执行。如果重试每次追加Paragraph，就会重复数据。`DocumentProcessor`先准备全部新切片，再在事务中删除旧切片并整体替换，因此同一文档重复执行不会追加。

## 23. 为什么备份必须真实恢复

`pg_dump`成功只证明生成文件，不证明文件完整、权限正确或版本兼容。恢复到独立临时库并核对关联记录，才能给出本次RTO/恢复证据。media还要单独备份。

## 24. CI怎样构建生产镜像

CI先跑139项既有回归和新增测试，再在Linux执行标准前端构建、Backend/Nginx镜像构建、Compose config、`nginx -t`、Migration、live和首页冒烟，并检查镜像不含`.env/.git/SQLite/media`。

## 25. 如何排查模型超时

从前端响应取得Request ID，在JSON日志找trace_id，再在Jaeger打开`llm.chat`或`embedding.batch`，对照`model_timeouts_total`和模型延迟。只记录异常类型/安全错误码，绝不把API Key或完整Prompt复制到工单。

## 26. 面试怎么讲

可以按“问题—设计—验证—边界”回答：同步SSE会占线程，所以选gthread并配置代理缓冲/超时；用Request ID+Trace跨HTTP/Celery；用Counter/Gauge/Histogram建立三类Dashboard与持续告警；用数据库心跳和幂等收敛处理Worker崩溃；最后用Linux构建、真实Compose、故障演练和独立恢复证明，而不是只说用了Docker和Prometheus。未完成的Docker实测与压测必须明确说是待验收，不能编数字。
