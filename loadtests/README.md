# Stage 8异步上传压测

该脚本只用于`docker-compose.stage08.yml`创建的临时PostgreSQL、Redis和MEDIA卷，Embedding固定走本地确定性实现，不读取真实模型密钥或真实文档。

## 准备数据

```powershell
docker compose -f docker-compose.stage08.yml exec backend python manage.py stage08_loadtest_data
```

## 阶梯压测

```powershell
New-Item -ItemType Directory -Force loadtests/results | Out-Null
docker compose -f docker-compose.stage08.yml --profile loadtest run --rm locust -f /mnt/locust/stage08_locustfile.py --host http://backend:8000 --headless -u 10 -r 10 -t 30s --csv /mnt/locust/results/warmup-10
docker compose -f docker-compose.stage08.yml --profile loadtest run --rm locust -f /mnt/locust/stage08_locustfile.py --host http://backend:8000 --headless -u 50 -r 25 -t 30s --csv /mnt/locust/results/warmup-50
docker compose -f docker-compose.stage08.yml --profile loadtest run --rm locust -f /mnt/locust/stage08_locustfile.py --host http://backend:8000 --headless -u 200 -r 20 -t 45s --csv /mnt/locust/results/burst-200
```

`-r 20`表示10秒内爬升到200个用户。每个虚拟用户只上传一个带唯一幂等键的小型TXT，客户端超时为30秒。报告必须以`POST async document upload`这一行作为上传接口口径，不能把登录和列表请求混入上传P95/P99。

## 证据和清理

```powershell
docker compose -f docker-compose.stage08.yml exec backend python manage.py shell -c "from api.models import Document,DocumentProcessingTask; print({'documents':Document.objects.filter(name__startswith='stage08-load-').count(),'tasks':DocumentProcessingTask.objects.filter(document__name__startswith='stage08-load-').count()})"
docker compose -f docker-compose.stage08.yml exec redis redis-cli -n 0 LLEN celery
docker stats --no-stream
docker compose -f docker-compose.stage08.yml exec backend python manage.py stage08_loadtest_data --cleanup
```

只有真实CSV证明超时为0时，才能写“200并发提交0超时”。清理命令只允许删除`stage08-`前缀用户/知识库，且本Compose使用独立临时数据库和MEDIA卷。
