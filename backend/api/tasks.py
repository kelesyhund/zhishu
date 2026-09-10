import random

from celery import shared_task
from django.conf import settings

from .services.document_tasks import (
    execute_document_processing_task,
    is_retryable_processing_error,
    mark_task_failed,
    mark_task_retrying,
)


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    soft_time_limit=settings.DOCUMENT_TASK_SOFT_TIME_LIMIT,
    time_limit=settings.DOCUMENT_TASK_TIME_LIMIT,
)
def process_document_task(self, processing_task_id: int):
    try:
        status = execute_document_processing_task(processing_task_id)
        return {"task_id": processing_task_id, "status": status}
    except Exception as exc:
        if is_retryable_processing_error(exc) and self.request.retries < self.max_retries:
            mark_task_retrying(processing_task_id, exc)
            countdown = min(60, 5 * (2**self.request.retries) + random.uniform(0, 2))
            raise self.retry(exc=exc, countdown=countdown, max_retries=self.max_retries)
        mark_task_failed(processing_task_id, exc)
        return {"task_id": processing_task_id, "status": "FAILURE"}
