from collections.abc import Callable

from django.conf import settings
from django.db import transaction

from api.models import Document, DocumentSection, Paragraph

from .document_chunking import chunking_signature, prepare_document_chunks
from .document_parser import CHUNKER_VERSION, PARSER_VERSION
from .embeddings import embed_texts
from .model_clients import ModelServiceError
from .model_resolution import active_embedding_signature
from .vector_storage import bulk_write_paragraph_embeddings, validate_vectors
from ..observability import traced


class DocumentProcessingCancelled(Exception):
    """任务被用户取消或所属资源已被删除。"""


ProgressCallback = Callable[[str, int], None]
CancellationCheck = Callable[[], bool]


def safe_document_error(exc: Exception) -> str:
    if isinstance(exc, ModelServiceError):
        return exc.message[:500]
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    if isinstance(exc, DocumentProcessingCancelled):
        return "文档处理已取消"
    return "文档处理失败，请稍后重试"


def process_document(
    document: Document,
    *,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
) -> Document:
    """解析并安全替换文档切片；异步和同步入口共用这一实现。"""

    def report(stage: str, progress: int):
        if progress_callback:
            progress_callback(stage, progress)

    def check_cancelled():
        if should_cancel and should_cancel():
            raise DocumentProcessingCancelled("文档处理已取消")

    had_paragraphs = document.paragraphs.exists()
    requested_strategy = document.chunk_strategy
    if not had_paragraphs:
        document.status = Document.Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message"])

    try:
        check_cancelled()
        report("READING", 10)
        with traced("document.parse", document_id=document.id):
            parsed, plan = prepare_document_chunks(document)
        check_cancelled()

        report("SPLITTING", 30)
        with traced("document.chunk", document_id=document.id, selected_count=len(plan.children)):
            children = list(plan.children)
        check_cancelled()

        report("EMBEDDING", 50)
        vectors: list[list[float]] = []
        batch_size = 32
        for start in range(0, len(children), batch_size):
            check_cancelled()
            batch = children[start : start + batch_size]
            with traced("embedding.batch", document_id=document.id, selected_count=len(batch)):
                vectors.extend(
                    embed_texts([chunk.embedding_content for chunk in batch], document.knowledge_base)
                )
            completed = min(len(children), start + len(batch))
            report("EMBEDDING", 50 + int((completed / len(children)) * 30))
        target_signature = active_embedding_signature(document.knowledge_base)
        validate_vectors(vectors)
        target_chunking_signature = chunking_signature(document)
        check_cancelled()

        report("SAVING", 85)
        with transaction.atomic(), traced(
            "paragraph.replace", document_id=document.id, selected_count=len(children)
        ):
            locked_document = (
                Document.objects.select_for_update()
                .get(pk=document.pk)
            )
            check_cancelled()
            locked_document.paragraphs.all().delete()
            locked_document.sections.all().delete()
            parent_rows = DocumentSection.objects.bulk_create(
                [
                    DocumentSection(
                        document=locked_document,
                        position=parent.position,
                        content=parent.content,
                        heading_path=list(parent.heading_path),
                        page_start=parent.page_start,
                        page_end=parent.page_end,
                        token_count=parent.token_count,
                        content_sha256=parent.content_sha256,
                        parser_version=PARSER_VERSION,
                        chunker_version=CHUNKER_VERSION,
                        source_block_ids=list(parent.source_block_ids),
                        structure_type=parent.structure_type,
                    )
                    for parent in plan.parents
                ]
            )
            parent_by_position = {parent.position: parent for parent in parent_rows}
            paragraph_rows = Paragraph.objects.bulk_create(
                [
                    Paragraph(
                        document=locked_document,
                        position=child.position,
                        content=child.content,
                        embedding=(
                            [] if settings.VECTOR_WRITE_MODE == "PGVECTOR"
                            else vectors[index]
                        ),
                        chunk_type=(
                            Paragraph.ChunkType.LEGACY
                            if requested_strategy == Document.ChunkStrategy.LEGACY
                            else Paragraph.ChunkType.CHILD
                        ),
                        parent_section=parent_by_position.get(child.parent_position),
                        heading_path=list(child.heading_path),
                        page_start=child.page_start,
                        page_end=child.page_end,
                        token_count=child.token_count,
                        content_sha256=child.content_sha256,
                        parser_version=PARSER_VERSION,
                        chunker_version=CHUNKER_VERSION,
                        source_block_ids=list(child.source_block_ids),
                        structure_type=child.structure_type,
                    )
                    for index, child in enumerate(children)
                ]
            )
            bulk_write_paragraph_embeddings(locked_document.knowledge_base, paragraph_rows, vectors)
            locked_document.status = Document.Status.SUCCESS
            locked_document.error_message = ""
            locked_document.paragraph_count = len(children)
            locked_document.parent_chunk_count = len(plan.parents)
            locked_document.embedding_signature = target_signature
            locked_document.source_sha256 = parsed.source_sha256
            locked_document.indexed_chunking_signature = target_chunking_signature
            locked_document.parser_version = PARSER_VERSION
            locked_document.chunker_version = CHUNKER_VERSION
            locked_document.parsing_warnings = list(plan.warnings)
            locked_document.save(
                update_fields=[
                    "status",
                    "error_message",
                    "paragraph_count",
                    "parent_chunk_count",
                    "embedding_signature",
                    "source_sha256",
                    "indexed_chunking_signature",
                    "parser_version",
                    "chunker_version",
                    "parsing_warnings",
                ]
            )
        with traced("document.finalize", document_id=document.id):
            report("DONE", 100)
    except DocumentProcessingCancelled:
        raise
    except Document.DoesNotExist as exc:
        raise DocumentProcessingCancelled("文档已被删除") from exc
    except Exception as exc:
        remaining_count = Paragraph.objects.filter(
            document_id=document.pk,
            chunk_type__in=(Paragraph.ChunkType.LEGACY, Paragraph.ChunkType.CHILD),
        ).count()
        safe_message = safe_document_error(exc)
        if had_paragraphs and remaining_count:
            Document.objects.filter(pk=document.pk).update(
                status=Document.Status.SUCCESS,
                error_message=f"重新处理失败，已保留旧切片：{safe_message}"[:500],
                paragraph_count=remaining_count,
            )
        else:
            Document.objects.filter(pk=document.pk).update(
                status=Document.Status.FAILURE,
                error_message=safe_message,
                paragraph_count=0,
            )
        raise

    return Document.objects.get(pk=document.pk)
