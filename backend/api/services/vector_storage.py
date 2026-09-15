import hashlib
import math
import os
from dataclasses import dataclass
from time import perf_counter

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Count
from django.utils import timezone

from api.models import (
    Document,
    EmbeddingSpace,
    KnowledgeBase,
    Paragraph,
    ParagraphEmbedding,
    VectorMigrationRun,
)
from ..observability import VECTOR_BACKFILL_ROWS_TOTAL, VECTOR_SPACE_COVERAGE_RATIO

from .model_resolution import active_embedding_signature


class VectorStorageError(Exception):
    def __init__(self, message: str, error_code: str = "VECTOR_STORAGE_ERROR"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True)
class BackfillBatchResult:
    status: str
    processed: int
    has_more: bool


def vector_modes() -> dict:
    return {
        "write_mode": settings.VECTOR_WRITE_MODE,
        "read_mode": settings.VECTOR_READ_MODE,
        "search_mode": settings.VECTOR_SEARCH_MODE,
        "shadow_sample_rate": settings.VECTOR_SHADOW_SAMPLE_RATE,
        "database_vendor": connection.vendor,
        "pgvector_available": connection.vendor == "postgresql",
    }


def embedding_space_signature(knowledge_base: KnowledgeBase) -> tuple[str, str, int, object | None]:
    config = knowledge_base.embedding_model_config
    if config:
        return active_embedding_signature(knowledge_base), config.model_name, config.revision, config
    env_model = os.getenv("EMBEDDING_MODEL", "").strip()
    if env_model and os.getenv("OPENAI_API_KEY", "").strip():
        digest = hashlib.sha256(env_model.encode("utf-8")).hexdigest()[:16]
        return f"environment:{digest}", env_model, 1, None
    return "local-hash-256", "local-hash-256", 1, None


def resolve_embedding_space(
    knowledge_base: KnowledgeBase,
    dimension: int,
    *,
    create: bool = False,
    ready: bool = False,
) -> EmbeddingSpace | None:
    if dimension < 1:
        raise VectorStorageError("向量维度无效", "VECTOR_DIMENSION_INVALID")
    signature, model_name, revision, config = embedding_space_signature(knowledge_base)
    lookup = {
        "workspace": knowledge_base.workspace,
        "signature": signature,
        "dimension": dimension,
    }
    if not create:
        queryset = EmbeddingSpace.objects.filter(**lookup)
        if ready:
            queryset = queryset.filter(status=EmbeddingSpace.Status.READY)
        return queryset.first()
    space, created = EmbeddingSpace.objects.get_or_create(
        **lookup,
        defaults={
            "model_config": config,
            "model_name": model_name,
            "revision": revision,
            "status": EmbeddingSpace.Status.READY if ready else EmbeddingSpace.Status.DISCOVERED,
        },
    )
    changes = []
    if space.model_name != model_name:
        space.model_name = model_name
        changes.append("model_name")
    if space.model_config_id != getattr(config, "id", None):
        space.model_config = config
        changes.append("model_config")
    if ready and space.status != EmbeddingSpace.Status.READY:
        space.status = EmbeddingSpace.Status.READY
        changes.append("status")
    if changes and not created:
        space.save(update_fields=[*changes, "updated_at"])
    return space


def validate_vectors(vectors: list[list[float]]) -> int:
    if not vectors or not vectors[0]:
        raise VectorStorageError("Embedding 未返回有效向量", "VECTOR_EMPTY")
    dimension = len(vectors[0])
    for vector in vectors:
        if len(vector) != dimension or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
            raise VectorStorageError("Embedding 向量维度或数值无效", "VECTOR_INVALID")
    return dimension


def bulk_write_paragraph_embeddings(
    knowledge_base: KnowledgeBase,
    paragraphs: list[Paragraph],
    vectors: list[list[float]],
) -> EmbeddingSpace | None:
    if settings.VECTOR_WRITE_MODE == "LEGACY":
        return None
    if len(paragraphs) != len(vectors):
        raise VectorStorageError("切片和向量数量不一致", "VECTOR_COUNT_MISMATCH")
    paragraph_ids = [paragraph.id for paragraph in paragraphs]
    if (
        any(paragraph_id is None for paragraph_id in paragraph_ids)
        or len(set(paragraph_ids)) != len(paragraph_ids)
        or Paragraph.objects.filter(
            id__in=paragraph_ids,
            document__knowledge_base=knowledge_base,
        ).count() != len(paragraph_ids)
    ):
        raise VectorStorageError("切片不属于目标知识库", "VECTOR_KNOWLEDGE_BASE_MISMATCH")
    dimension = validate_vectors(vectors)
    space = resolve_embedding_space(knowledge_base, dimension, create=True, ready=True)
    ParagraphEmbedding.objects.bulk_create(
        [
            ParagraphEmbedding(
                paragraph=paragraph,
                knowledge_base=knowledge_base,
                space=space,
                embedding=vector,
                dimension=dimension,
                content_hash=paragraph.content_sha256 or hashlib.sha256(paragraph.content.encode("utf-8")).hexdigest(),
            )
            for paragraph, vector in zip(paragraphs, vectors)
        ]
    )
    return space


def eligible_legacy_paragraphs(workspace, signature: str, dimension: int):
    queryset = Paragraph.objects.filter(
        document__knowledge_base__workspace=workspace,
        document__status=Document.Status.SUCCESS,
        chunk_type__in=(Paragraph.ChunkType.LEGACY, Paragraph.ChunkType.CHILD),
    )
    if signature.startswith("config:"):
        queryset = queryset.filter(document__embedding_signature=signature)
    return queryset.exclude(embedding=[]).filter(id__gt=0)


def create_migration_run(workspace, space: EmbeddingSpace, user, batch_size: int = 200):
    if space.workspace_id != workspace.id:
        raise VectorStorageError("向量空间不存在", "VECTOR_SPACE_NOT_FOUND")
    batch_size = max(10, min(int(batch_size), 2000))
    total = eligible_legacy_paragraphs(workspace, space.signature, space.dimension).count()
    try:
        with transaction.atomic():
            return VectorMigrationRun.objects.create(
                workspace=workspace,
                space=space,
                total_count=total,
                batch_size=batch_size,
                created_by=user,
            )
    except IntegrityError:
        active = VectorMigrationRun.objects.filter(
            space=space,
            status__in=(
                VectorMigrationRun.Status.PENDING,
                VectorMigrationRun.Status.RUNNING,
                VectorMigrationRun.Status.CANCEL_REQUESTED,
            ),
        ).first()
        if active:
            return active
        raise


def execute_backfill_batch(run_id: int) -> BackfillBatchResult:
    run = VectorMigrationRun.objects.select_related("space", "workspace").filter(pk=run_id).first()
    if not run:
        return BackfillBatchResult("MISSING", 0, False)
    if run.status in {VectorMigrationRun.Status.SUCCESS, VectorMigrationRun.Status.CANCELLED}:
        return BackfillBatchResult(run.status, 0, False)
    if run.status == VectorMigrationRun.Status.CANCEL_REQUESTED:
        VectorMigrationRun.objects.filter(pk=run.id).update(
            status=VectorMigrationRun.Status.CANCELLED, finished_at=timezone.now(), updated_at=timezone.now()
        )
        return BackfillBatchResult(VectorMigrationRun.Status.CANCELLED, 0, False)

    if run.started_at is None:
        run.started_at = timezone.now()
    run.status = VectorMigrationRun.Status.RUNNING
    run.error_message = ""
    run.save(update_fields=["status", "started_at", "error_message", "updated_at"])
    rows = list(
        eligible_legacy_paragraphs(run.workspace, run.space.signature, run.space.dimension)
        .filter(id__gt=run.cursor_id)
        .select_related("document")
        .order_by("id")[: run.batch_size]
    )
    if not rows:
        run.refresh_from_db()
        run.status = (
            VectorMigrationRun.Status.FAILURE
            if run.failed_count
            else VectorMigrationRun.Status.SUCCESS
        )
        run.error_message = (
            f"{run.failed_count} 条Legacy向量维度或数值无效，请修复后重试"
            if run.failed_count
            else ""
        )
        run.finished_at = timezone.now()
        run.space.status = (
            EmbeddingSpace.Status.DEGRADED
            if run.failed_count
            else EmbeddingSpace.Status.READY
        )
        run.space.save(update_fields=["status", "updated_at"])
        run.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
        return BackfillBatchResult(run.status, 0, False)

    succeeded = failed = skipped = 0
    last_id = rows[-1].id
    with transaction.atomic():
        valid_rows = []
        for paragraph in rows:
            vector = paragraph.embedding
            if not isinstance(vector, list) or len(vector) != run.space.dimension:
                failed += 1
                continue
            if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
                failed += 1
                continue
            valid_rows.append(paragraph)

        existing_ids = set(
            ParagraphEmbedding.objects.filter(
                space=run.space,
                paragraph_id__in=[paragraph.id for paragraph in valid_rows],
            ).values_list("paragraph_id", flat=True)
        )
        now = timezone.now()
        ParagraphEmbedding.objects.bulk_create(
            [
                ParagraphEmbedding(
                    paragraph=paragraph,
                    knowledge_base=paragraph.document.knowledge_base,
                    space=run.space,
                    embedding=paragraph.embedding,
                    dimension=run.space.dimension,
                    content_hash=paragraph.content_sha256 or hashlib.sha256(paragraph.content.encode("utf-8")).hexdigest(),
                    created_at=now,
                    updated_at=now,
                )
                for paragraph in valid_rows
            ],
            batch_size=run.batch_size,
            update_conflicts=True,
            update_fields=["embedding", "dimension", "content_hash", "updated_at"],
            unique_fields=["paragraph", "space"],
        )
        succeeded = len(valid_rows) - len(existing_ids)
        skipped = len(existing_ids)
        VectorMigrationRun.objects.filter(pk=run.id).update(
            cursor_id=last_id,
            succeeded_count=run.succeeded_count + succeeded,
            failed_count=run.failed_count + failed,
            skipped_count=run.skipped_count + skipped,
            updated_at=timezone.now(),
        )
    for outcome, count in (("succeeded", succeeded), ("failed", failed), ("skipped", skipped)):
        if count:
            VECTOR_BACKFILL_ROWS_TOTAL.labels(outcome).inc(count)
    return BackfillBatchResult(VectorMigrationRun.Status.RUNNING, len(rows), len(rows) == run.batch_size)


def dispatch_migration_run(run: VectorMigrationRun) -> VectorMigrationRun:
    from api.tasks import process_vector_migration_task

    try:
        result = process_vector_migration_task.apply_async(args=[run.id])
        VectorMigrationRun.objects.filter(pk=run.id).update(
            celery_task_id=result.id or "", updated_at=timezone.now()
        )
    except Exception:
        VectorMigrationRun.objects.filter(pk=run.id).update(
            status=VectorMigrationRun.Status.FAILURE,
            error_message="向量迁移任务队列暂时不可用，请稍后重试",
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
    return VectorMigrationRun.objects.get(pk=run.id)


def retry_migration_run(run: VectorMigrationRun) -> VectorMigrationRun:
    if run.status not in {
        VectorMigrationRun.Status.FAILURE,
        VectorMigrationRun.Status.CANCELLED,
    }:
        return run
    active = VectorMigrationRun.objects.filter(
        space=run.space,
        status__in=(
            VectorMigrationRun.Status.PENDING,
            VectorMigrationRun.Status.RUNNING,
            VectorMigrationRun.Status.CANCEL_REQUESTED,
        ),
    ).exclude(pk=run.pk).first()
    if active:
        return active
    was_failure = run.status == VectorMigrationRun.Status.FAILURE
    run.status = VectorMigrationRun.Status.PENDING
    run.error_message = ""
    run.finished_at = None
    if was_failure:
        run.cursor_id = 0
        run.succeeded_count = 0
        run.failed_count = 0
        run.skipped_count = 0
    run.save(update_fields=[
        "status", "error_message", "finished_at", "cursor_id", "succeeded_count",
        "failed_count", "skipped_count", "updated_at",
    ])
    return dispatch_migration_run(run)


def cancel_migration_run(run: VectorMigrationRun) -> VectorMigrationRun:
    if run.status in {VectorMigrationRun.Status.PENDING, VectorMigrationRun.Status.RUNNING}:
        run.status = VectorMigrationRun.Status.CANCEL_REQUESTED
        run.save(update_fields=["status", "updated_at"])
    return run


def workspace_vector_status(workspace) -> dict:
    legacy_count = eligible_legacy_paragraphs(workspace, "", 1).count()
    vector_count = ParagraphEmbedding.objects.filter(space__workspace=workspace).count()
    covered = ParagraphEmbedding.objects.filter(space__workspace=workspace).values("paragraph_id").distinct().count()
    denominator = legacy_count or 0
    coverage = round(covered / denominator, 6) if denominator else 1.0
    VECTOR_SPACE_COVERAGE_RATIO.labels("covered").set(coverage)
    return {
        **vector_modes(),
        "legacy_vector_count": denominator,
        "pgvector_row_count": vector_count,
        "covered_paragraph_count": covered,
        "coverage_ratio": coverage,
        "spaces": list(
            EmbeddingSpace.objects.filter(workspace=workspace)
            .annotate(vector_count=Count("paragraph_embeddings"))
            .values("id", "signature", "model_name", "revision", "dimension", "status", "indexed", "vector_count")
        ),
    }
