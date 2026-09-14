import socket
import threading
from time import monotonic

from celery.signals import heartbeat_sent, worker_ready, worker_shutdown
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .metrics_compat import start_http_server

from .observability import (
    CELERY_OLDEST_TASK_AGE,
    CELERY_QUEUE_DEPTH,
    CELERY_WORKERS_ONLINE,
    initialize_tracing,
)


_server_started = False
_guard = threading.Lock()
_last_heartbeat_update = 0.0


def worker_name() -> str:
    return socket.gethostname()[:64]


def sample_oldest_task_age() -> None:
    """Refresh the age gauge in the worker process that owns the metrics server."""
    try:
        from .models import DocumentProcessingTask

        oldest = (
            DocumentProcessingTask.objects.filter(status__in=DocumentProcessingTask.ACTIVE_STATUSES)
            .order_by("created_at")
            .only("created_at")
            .first()
        )
        age = max(0, (timezone.now() - oldest.created_at).total_seconds()) if oldest else 0
        CELERY_OLDEST_TASK_AGE.set(age)
    except Exception:
        # A telemetry query must never interrupt Celery heartbeats.
        pass


@worker_ready.connect(weak=False)
def on_worker_ready(**kwargs):
    global _server_started
    initialize_tracing()
    name = worker_name()
    cache.set(f"stage15:worker-heartbeat:{name}", "online", settings.WORKER_HEARTBEAT_TTL_SECONDS)
    CELERY_WORKERS_ONLINE.labels(name).set(1)
    sample_oldest_task_age()
    with _guard:
        if not _server_started:
            try:
                start_http_server(settings.METRICS_PORT)
                _server_started = True
            except OSError:
                # A metrics endpoint failure must not prevent the worker from accepting tasks.
                pass


@heartbeat_sent.connect(weak=False)
def on_worker_heartbeat(**kwargs):
    global _last_heartbeat_update
    now = monotonic()
    if now - _last_heartbeat_update < 10:
        return
    _last_heartbeat_update = now
    name = worker_name()
    cache.set(f"stage15:worker-heartbeat:{name}", "online", settings.WORKER_HEARTBEAT_TTL_SECONDS)
    CELERY_WORKERS_ONLINE.labels(name).set(1)
    try:
        import redis

        client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=1)
        CELERY_QUEUE_DEPTH.labels("celery").set(int(client.llen("celery")))
    except Exception:
        pass
    sample_oldest_task_age()


@worker_shutdown.connect(weak=False)
def on_worker_shutdown(**kwargs):
    name = worker_name()
    CELERY_WORKERS_ONLINE.labels(name).set(0)
    cache.delete(f"stage15:worker-heartbeat:{name}")
