from dataclasses import dataclass
from uuid import uuid4

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from api.models import Document, DocumentProcessingTask

from .document_processor import DocumentProcessingCancelled, process_document, safe_document_error
from .document_task_lock import DocumentTaskLockError, document_processing_lock
from .model_clients import ModelServiceError


@dataclass(frozen=True)
class TaskDispatchResult:
    processing_task: DocumentProcessingTask
    dispatched: bool


RETRYABLE_MODEL_CODES = {"TIMEOUT", "RATE_LIMITED", "CONNECTION_FAILED"}


def is_retryable_processing_error(exc: Exception) -> bool:
    if isinstance(exc, ModelServiceError):
        return exc.error_code in RETRYABLE_MODEL_CODES
    return isinstance(exc, (DocumentTaskLockError, OperationalError, SoftTimeLimitExceeded))


def create_processing_task(
    document: Document,
    task_type: str,
    *,
    idempotency_key: str = "",
) -> tuple[DocumentProcessingTask, bool]:
    normalized_key = idempotency_key.strip()[:100]
    if normalized_key:
        existing = document.processing_tasks.filter(idempotency_key=normalized_key).first()
        if existing:
            return existing, False
    try:
        with transaction.atomic():
            task = DocumentProcessingTask.objects.create(
                document=document,
                task_type=task_type,
                idempotency_key=normalized_key,
            )
        return task, True
    except IntegrityError:
        active = document.processing_tasks.filter(
            status__in=DocumentProcessingTask.ACTIVE_STATUSES
        ).first()
        if active:
            return active, False
        raise


def _mark_enqueue_failed(task_id: int):
    now = timezone.now()
    task = DocumentProcessingTask.objects.filter(pk=task_id).select_related("document").first()
    if not task:
        return
    DocumentProcessingTask.objects.filter(pk=task_id).update(
        status=DocumentProcessingTask.Status.ENQUEUE_FAILED,
        current_stage=DocumentProcessingTask.Stage.FAILED,
        error_message="任务队列暂时不可用，请稍后重试",
        finished_at=now,
        updated_at=now,
    )
    if task.task_type == DocumentProcessingTask.TaskType.UPLOAD:
        Document.objects.filter(pk=task.document_id).update(
            status=Document.Status.FAILURE,
            error_message="任务队列暂时不可用，请稍后重试",
        )


def dispatch_processing_task(task_id: int) -> TaskDispatchResult:
    from api.tasks import process_document_task

    dispatched = True

    def send():
        nonlocal dispatched
        try:
            result = process_document_task.apply_async(args=[task_id])
            DocumentProcessingTask.objects.filter(pk=task_id).update(
                celery_task_id=result.id or "",
                updated_at=timezone.now(),
            )
        except Exception:
            dispatched = False
            _mark_enqueue_failed(task_id)

    if settings.CELERY_TASK_ALWAYS_EAGER:
        send()
    else:
        transaction.on_commit(send)
    return TaskDispatchResult(
        processing_task=DocumentProcessingTask.objects.get(pk=task_id),
        dispatched=dispatched,
    )


def _update_progress(task_id: int, stage: str, progress: int):
    task = DocumentProcessingTask.objects.filter(pk=task_id).only("progress", "status").first()
    if not task or task.status not in DocumentProcessingTask.ACTIVE_STATUSES:
        return
    normalized_progress = max(task.progress, min(100, max(0, progress)))
    DocumentProcessingTask.objects.filter(pk=task_id).update(
        current_stage=stage,
        progress=normalized_progress,
        updated_at=timezone.now(),
    )


def _should_cancel(task_id: int) -> bool:
    status = DocumentProcessingTask.objects.filter(pk=task_id).values_list("status", flat=True).first()
    return status in {
        None,
        DocumentProcessingTask.Status.CANCEL_REQUESTED,
        DocumentProcessingTask.Status.CANCELLED,
    }


def mark_task_cancelled(task_id: int):
    now = timezone.now()
    task = DocumentProcessingTask.objects.filter(pk=task_id).select_related("document").first()
    DocumentProcessingTask.objects.filter(pk=task_id).update(
        status=DocumentProcessingTask.Status.CANCELLED,
        current_stage=DocumentProcessingTask.Stage.CANCELLED,
        error_message="",
        finished_at=now,
        updated_at=now,
    )
    if (
        task
        and task.task_type == DocumentProcessingTask.TaskType.UPLOAD
        and not task.document.paragraphs.exists()
    ):
        Document.objects.filter(pk=task.document_id).update(
            status=Document.Status.FAILURE,
            error_message="文档处理已取消",
            paragraph_count=0,
        )


def execute_document_processing_task(task_id: int) -> str:
    task = (
        DocumentProcessingTask.objects.select_related(
            "document__knowledge_base__embedding_model_config"
        )
        .filter(pk=task_id)
        .first()
    )
    if not task:
        return "MISSING"
    if task.status == DocumentProcessingTask.Status.SUCCESS:
        return task.status
    if task.status in {
        DocumentProcessingTask.Status.CANCEL_REQUESTED,
        DocumentProcessingTask.Status.CANCELLED,
    }:
        mark_task_cancelled(task_id)
        return DocumentProcessingTask.Status.CANCELLED

    try:
        with document_processing_lock(task.document_id):
            with transaction.atomic():
                locked_task = DocumentProcessingTask.objects.select_for_update().get(pk=task_id)
                if locked_task.status == DocumentProcessingTask.Status.SUCCESS:
                    return locked_task.status
                if locked_task.status in {
                    DocumentProcessingTask.Status.CANCEL_REQUESTED,
                    DocumentProcessingTask.Status.CANCELLED,
                }:
                    mark_task_cancelled(task_id)
                    return DocumentProcessingTask.Status.CANCELLED
                locked_task.status = DocumentProcessingTask.Status.PROCESSING
                locked_task.current_stage = DocumentProcessingTask.Stage.READING
                locked_task.error_message = ""
                locked_task.progress = max(locked_task.progress, 5)
                locked_task.attempt_count = F("attempt_count") + 1
                if locked_task.started_at is None:
                    locked_task.started_at = timezone.now()
                locked_task.finished_at = None
                locked_task.save(
                    update_fields=[
                        "status",
                        "current_stage",
                        "error_message",
                        "progress",
                        "attempt_count",
                        "started_at",
                        "finished_at",
                        "updated_at",
                    ]
                )

            document = Document.objects.select_related(
                "knowledge_base__embedding_model_config"
            ).get(pk=task.document_id)
            process_document(
                document,
                progress_callback=lambda stage, progress: _update_progress(task_id, stage, progress),
                should_cancel=lambda: _should_cancel(task_id),
            )
            now = timezone.now()
            DocumentProcessingTask.objects.filter(pk=task_id).update(
                status=DocumentProcessingTask.Status.SUCCESS,
                current_stage=DocumentProcessingTask.Stage.DONE,
                progress=100,
                error_message="",
                finished_at=now,
                updated_at=now,
            )
            return DocumentProcessingTask.Status.SUCCESS
    except DocumentProcessingCancelled:
        mark_task_cancelled(task_id)
        return DocumentProcessingTask.Status.CANCELLED


def mark_task_retrying(task_id: int, exc: Exception):
    DocumentProcessingTask.objects.filter(pk=task_id).update(
        status=DocumentProcessingTask.Status.RETRYING,
        current_stage=DocumentProcessingTask.Stage.WAITING,
        error_message=safe_document_error(exc),
        updated_at=timezone.now(),
    )


def mark_task_failed(task_id: int, exc: Exception):
    now = timezone.now()
    DocumentProcessingTask.objects.filter(pk=task_id).update(
        status=DocumentProcessingTask.Status.FAILURE,
        current_stage=DocumentProcessingTask.Stage.FAILED,
        error_message=safe_document_error(exc),
        finished_at=now,
        updated_at=now,
    )


def retry_processing_task(task: DocumentProcessingTask) -> DocumentProcessingTask:
    if task.status == DocumentProcessingTask.Status.ENQUEUE_FAILED:
        with transaction.atomic():
            task = DocumentProcessingTask.objects.select_for_update().get(pk=task.pk)
            active = (
                DocumentProcessingTask.objects.filter(
                    document_id=task.document_id,
                    status__in=DocumentProcessingTask.ACTIVE_STATUSES,
                )
                .exclude(pk=task.pk)
                .first()
            )
            if active:
                return active
            task.status = DocumentProcessingTask.Status.PENDING
            task.current_stage = DocumentProcessingTask.Stage.WAITING
            task.progress = 0
            task.error_message = ""
            task.finished_at = None
            task.celery_task_id = ""
            task.save(
                update_fields=[
                    "status",
                    "current_stage",
                    "progress",
                    "error_message",
                    "finished_at",
                    "celery_task_id",
                    "updated_at",
                ]
            )
        return dispatch_processing_task(task.id).processing_task

    new_task, created = create_processing_task(
        task.document,
        DocumentProcessingTask.TaskType.REPROCESS,
        idempotency_key=f"retry:{task.id}:{uuid4().hex}",
    )
    if created:
        return dispatch_processing_task(new_task.id).processing_task
    return new_task


def request_task_cancel(task: DocumentProcessingTask) -> DocumentProcessingTask:
    if task.status == DocumentProcessingTask.Status.ENQUEUE_FAILED:
        mark_task_cancelled(task.id)
        return DocumentProcessingTask.objects.get(pk=task.id)
    if task.status not in DocumentProcessingTask.ACTIVE_STATUSES:
        return task
    if task.status in {
        DocumentProcessingTask.Status.PENDING,
        DocumentProcessingTask.Status.RETRYING,
    }:
        mark_task_cancelled(task.id)
        task = DocumentProcessingTask.objects.get(pk=task.id)
    else:
        task.status = DocumentProcessingTask.Status.CANCEL_REQUESTED
        task.current_stage = DocumentProcessingTask.Stage.CANCELLING
        task.error_message = ""
        task.save(update_fields=["status", "current_stage", "error_message", "updated_at"])
    if task.celery_task_id:
        try:
            from config.celery import app

            app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass
    return task
