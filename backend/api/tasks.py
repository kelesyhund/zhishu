import random
import socket
from time import perf_counter

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .telemetry_compat import attach, detach, extract

from .services.document_tasks import (
    execute_document_processing_task,
    is_retryable_processing_error,
    mark_task_failed,
    mark_task_retrying,
)
from .models import DocumentProcessingTask, VectorMigrationRun
from .observability import (
    CELERY_OLDEST_TASK_AGE,
    CELERY_QUEUE_DEPTH,
    CELERY_TASKS_ACTIVE,
    CELERY_TASKS_FAILED_TOTAL,
    CELERY_TASKS_RETRIED_TOTAL,
    CELERY_WORKERS_ONLINE,
    DOCUMENT_TASK_DURATION,
    DOCUMENT_TASK_FAILURES_TOTAL,
    DOCUMENT_TASK_QUEUE_WAIT,
    DOCUMENT_TASK_RETRIES_TOTAL,
    DOCUMENT_TASKS_IN_PROGRESS,
    DOCUMENT_TASKS_TOTAL,
    VECTOR_BACKFILL_DURATION,
    LOG_CONTEXT,
    REQUEST_ID,
    normalized_error_code,
    traced,
)
from .services.task_recovery import reconcile_stale_processing_tasks
from .services.vector_storage import execute_backfill_batch


@shared_task(
    name="api.tasks.process_document_task",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    soft_time_limit=settings.DOCUMENT_TASK_SOFT_TIME_LIMIT,
    time_limit=settings.DOCUMENT_TASK_TIME_LIMIT,
)
def process_document_task(self, processing_task_id: int):
    task_row = DocumentProcessingTask.objects.filter(pk=processing_task_id).first()
    task_type = task_row.task_type if task_row else "UNKNOWN"
    headers = dict(getattr(self.request, "headers", {}) or {})
    parent_context = extract(headers)
    context_token = attach(parent_context)
    request_token = REQUEST_ID.set(str(headers.get("x-request-id", ""))[:64])
    log_token = LOG_CONTEXT.set({"request_id": REQUEST_ID.get(), "task_id": processing_task_id})
    started = perf_counter()
    DOCUMENT_TASKS_IN_PROGRESS.labels(task_type).inc()
    CELERY_TASKS_ACTIVE.labels("document.process").inc()
    if task_row:
        DOCUMENT_TASK_QUEUE_WAIT.labels(task_type).observe(
            max(0, (timezone.now() - task_row.created_at).total_seconds())
        )
    try:
        with traced(
            "celery.document.process",
            task_id=processing_task_id,
            task_type=task_type,
            retry_count=int(self.request.retries),
        ):
            status = execute_document_processing_task(processing_task_id)
        DOCUMENT_TASKS_TOTAL.labels(task_type, status.lower()).inc()
        return {"task_id": processing_task_id, "status": status}
    except Exception as exc:
        error_code = normalized_error_code(getattr(exc, "error_code", type(exc).__name__))
        if is_retryable_processing_error(exc) and self.request.retries < self.max_retries:
            mark_task_retrying(processing_task_id, exc)
            DOCUMENT_TASK_RETRIES_TOTAL.labels(task_type, error_code).inc()
            CELERY_TASKS_RETRIED_TOTAL.labels("document.process").inc()
            countdown = min(60, 5 * (2**self.request.retries) + random.uniform(0, 2))
            raise self.retry(exc=exc, countdown=countdown, max_retries=self.max_retries)
        mark_task_failed(processing_task_id, exc)
        DOCUMENT_TASKS_TOTAL.labels(task_type, "failure").inc()
        DOCUMENT_TASK_FAILURES_TOTAL.labels(task_type, error_code).inc()
        CELERY_TASKS_FAILED_TOTAL.labels("document.process").inc()
        return {"task_id": processing_task_id, "status": "FAILURE"}
    finally:
        DOCUMENT_TASK_DURATION.labels(task_type).observe(max(0, perf_counter() - started))
        DOCUMENT_TASKS_IN_PROGRESS.labels(task_type).dec()
        CELERY_TASKS_ACTIVE.labels("document.process").dec()
        LOG_CONTEXT.reset(log_token)
        REQUEST_ID.reset(request_token)
        detach(context_token)


@shared_task(name="api.tasks.record_worker_heartbeat", ignore_result=True)
def record_worker_heartbeat():
    name = socket.gethostname()[:64]
    cache.set(f"stage15:worker-heartbeat:{name}", "online", settings.WORKER_HEARTBEAT_TTL_SECONDS)
    CELERY_WORKERS_ONLINE.labels(name).set(1)
    try:
        import redis

        client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=1)
        CELERY_QUEUE_DEPTH.labels("celery").set(int(client.llen("celery")))
    except Exception:
        pass
    oldest = DocumentProcessingTask.objects.filter(
        status__in=DocumentProcessingTask.ACTIVE_STATUSES
    ).order_by("created_at").first()
    CELERY_OLDEST_TASK_AGE.set(
        max(0, (timezone.now() - oldest.created_at).total_seconds()) if oldest else 0
    )
    return {"worker": name, "status": "online"}


@shared_task(name="api.tasks.reconcile_stale_document_tasks", ignore_result=True)
def reconcile_stale_document_tasks():
    return {"converged": reconcile_stale_processing_tasks()}


@shared_task(name="api.tasks.process_vector_migration", bind=True, acks_late=True, max_retries=3)
def process_vector_migration_task(self, migration_run_id: int):
    scope = VectorMigrationRun.objects.filter(pk=migration_run_id).values_list("workspace_id", "space_id").first()
    if not scope:
        return {"migration_run_id": migration_run_id, "status": "MISSING"}
    lock_key = f"stage16:vector-migration:{scope[0]}:{scope[1]}"
    if not cache.add(lock_key, self.request.id or "worker", timeout=120):
        return {"migration_run_id": migration_run_id, "status": "LOCKED"}
    try:
        batch_started = perf_counter()
        with traced("vector.backfill.batch", migration_run_id=migration_run_id):
            result = execute_backfill_batch(migration_run_id)
        VECTOR_BACKFILL_DURATION.observe(max(0, perf_counter() - batch_started))
        if result.status == "RUNNING" and settings.CELERY_TASK_ALWAYS_EAGER:
            while result.status == "RUNNING":
                result = execute_backfill_batch(migration_run_id)
        elif result.status == "RUNNING":
            self.apply_async(args=[migration_run_id], countdown=0)
        return {"migration_run_id": migration_run_id, "status": result.status, "processed": result.processed}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(60, 5 * (2 ** self.request.retries)))
        VectorMigrationRun.objects.filter(pk=migration_run_id).update(
            status=VectorMigrationRun.Status.FAILURE,
            error_message="向量迁移失败，请检查数据库与向量数据",
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return {"migration_run_id": migration_run_id, "status": "FAILURE"}
    finally:
        cache.delete(lock_key)
