from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.models import Document, DocumentProcessingTask

from ..observability import DOCUMENT_TASK_STALE_TOTAL


STALE_MESSAGE = "任务长时间无心跳，已安全停止；旧切片（如有）保持可用"


def reconcile_stale_processing_tasks(*, now=None) -> int:
    """Idempotently converge abandoned active tasks without deleting usable paragraphs."""
    current = now or timezone.now()
    cutoff = current - timedelta(seconds=settings.STALE_TASK_THRESHOLD_SECONDS)
    candidate_ids = list(
        DocumentProcessingTask.objects.filter(
            status__in=DocumentProcessingTask.ACTIVE_STATUSES,
            updated_at__lt=cutoff,
        ).values_list("id", flat=True)
    )
    converged = 0
    for task_id in candidate_ids:
        with transaction.atomic():
            task = (
                DocumentProcessingTask.objects.select_for_update()
                .select_related("document")
                .filter(pk=task_id)
                .first()
            )
            if not task or task.status not in DocumentProcessingTask.ACTIVE_STATUSES or task.updated_at >= cutoff:
                continue
            previous_status = task.status
            cancelled = previous_status == DocumentProcessingTask.Status.CANCEL_REQUESTED
            task.status = (
                DocumentProcessingTask.Status.CANCELLED
                if cancelled else DocumentProcessingTask.Status.FAILURE
            )
            task.current_stage = (
                DocumentProcessingTask.Stage.CANCELLED
                if cancelled else DocumentProcessingTask.Stage.FAILED
            )
            task.error_message = "" if cancelled else STALE_MESSAGE
            task.finished_at = current
            task.save(
                update_fields=["status", "current_stage", "error_message", "finished_at", "updated_at"]
            )
            has_paragraphs = task.document.paragraphs.exists()
            if has_paragraphs:
                Document.objects.filter(pk=task.document_id).update(
                    status=Document.Status.SUCCESS,
                    error_message="" if cancelled else STALE_MESSAGE,
                )
            else:
                Document.objects.filter(pk=task.document_id).update(
                    status=Document.Status.FAILURE,
                    error_message="文档处理已取消" if cancelled else STALE_MESSAGE,
                    paragraph_count=0,
                )
            DOCUMENT_TASK_STALE_TOTAL.labels(task.task_type, previous_status).inc()
            converged += 1
    return converged
