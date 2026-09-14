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
from .models import DocumentProcessingTask
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
    LOG_CONTEXT,
    REQUEST_ID,
    normalized_error_code,
    traced,
)
from .services.task_recovery import reconcile_stale_processing_tasks


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
