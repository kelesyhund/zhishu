# Stage 15生产拓扑压测口径

只对明确的临时环境执行。普通 API 客户端超时 5 秒，上传提交 10 秒，SSE 首 Token 10 秒、总请求 120 秒。场景 C 的 202 仅表示提交成功；压测后必须执行 `stage15_task_outcomes.py` 统计最终状态。

```powershell
$env:STAGE15_AUTH_TOKEN='仅本机临时Token'
$env:STAGE15_WORKSPACE_ID='临时工作空间ID'
$env:STAGE15_KNOWLEDGE_ID='临时知识库ID'

# A：分别执行 10/25/50/100；用 --class-picker 或 Locust UI只选择 Stage15ApiUser
locust -f loadtests/stage15_locustfile.py --host http://127.0.0.1:18080 --headless -u 10 -r 10 -t 60s --csv loadtests/results/stage15-api-10 Stage15ApiUser

# B：只使用无费用、固定延迟和固定长度的Mock配置；分别执行5/10/20/50
locust -f loadtests/stage15_locustfile.py --host http://127.0.0.1:18080 --headless -u 5 -r 5 -t 60s --csv loadtests/results/stage15-sse-5 Stage15SseUser

# C：分别执行10/25/50/100/200；每个用户仅提交一次小文本
locust -f loadtests/stage15_locustfile.py --host http://127.0.0.1:18080 --headless -u 10 -r 10 -t 30s --csv loadtests/results/stage15-upload-10 Stage15UploadUser
py -3.10 loadtests/stage15_task_outcomes.py
```

每次报告必须附：时间、commit与dirty状态、CPU/内存、容器资源、Web/Worker并发、PostgreSQL/Redis配置、Mock参数、样本/成功/失败/超时、吞吐、P50/P95/P99和原始CSV。没有实际执行时不得填写数值。
