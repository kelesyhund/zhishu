from dataclasses import dataclass
from time import perf_counter

from django.conf import settings
from django.db import connection, transaction
from django.db.models.functions import Cast
from pgvector.django import CosineDistance, VectorField

from api.models import Document, EmbeddingSpace, Paragraph, ParagraphEmbedding

from ..observability import traced
from ..observability import VECTOR_CANDIDATES_TOTAL, VECTOR_QUERIES_TOTAL, VECTOR_QUERY_DURATION
from .vector_retrieval import VectorScore, normalize_vector_score
from .vector_storage import resolve_embedding_space


@dataclass(frozen=True)
class PgvectorCandidates:
    scores: dict[int, VectorScore]
    paragraph_ids: tuple[int, ...]
    latency_ms: float
    fallback_code: str = ""


def query_pgvector_candidates(knowledge_base, query_vector, limit: int) -> PgvectorCandidates:
    if connection.vendor != "postgresql":
        VECTOR_QUERIES_TOTAL.labels("pgvector", settings.VECTOR_SEARCH_MODE, "fallback").inc()
        return PgvectorCandidates({}, (), 0, "DATABASE_NOT_POSTGRESQL")
    dimension = len(query_vector)
    space = resolve_embedding_space(knowledge_base, dimension, ready=True)
    if not space:
        VECTOR_QUERIES_TOTAL.labels("pgvector", settings.VECTOR_SEARCH_MODE, "fallback").inc()
        return PgvectorCandidates({}, (), 0, "SPACE_NOT_READY")
    if settings.VECTOR_SEARCH_MODE == "HNSW" and not space.indexed:
        VECTOR_QUERIES_TOTAL.labels("pgvector", "HNSW", "fallback").inc()
        return PgvectorCandidates({}, (), 0, "INDEX_NOT_READY")
    started = perf_counter()
    with traced(
        "vector.query.hnsw" if settings.VECTOR_SEARCH_MODE == "HNSW" else "vector.query.exact",
        dimension=dimension,
        selected_count=limit,
    ):
        with transaction.atomic():
            with connection.cursor() as cursor:
                if settings.VECTOR_SEARCH_MODE == "HNSW":
                    cursor.execute("SET LOCAL hnsw.ef_search = %s", [settings.VECTOR_HNSW_EF_SEARCH])
                    cursor.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                else:
                    # Once an HNSW index exists PostgreSQL may otherwise use it for the
                    # so-called EXACT path as well.  Disabling index scans only inside
                    # this transaction preserves a genuine sequential exact baseline.
                    cursor.execute("SET LOCAL enable_indexscan = off")
            vector_expression = Cast("embedding", VectorField(dimensions=dimension))
            queryset = ParagraphEmbedding.objects.filter(
                space=space,
                dimension=dimension,
                knowledge_base=knowledge_base,
            )
            queryset = (
                queryset.annotate(distance=CosineDistance(vector_expression, query_vector))
                # pgvector requires the distance operator to be the sole ORDER BY
                # expression for HNSW to be considered by PostgreSQL's planner.
                .order_by("distance")[: max(100, min(limit, 100) * 4)]
            )
            rows = list(queryset.values_list("paragraph_id", "distance"))
    # Keep the vector scan join-free so PostgreSQL can use HNSW.  Resource and
    # lifecycle checks are applied to the bounded candidate set immediately
    # afterwards; the knowledge_base FK on ParagraphEmbedding prevents any
    # cross-knowledge-base candidate leakage before this second query.
    eligible_ids = set(
        Paragraph.objects.filter(
            id__in=[paragraph_id for paragraph_id, _ in rows],
            document__knowledge_base=knowledge_base,
            document__status=Document.Status.SUCCESS,
            chunk_type__in=(Paragraph.ChunkType.LEGACY, Paragraph.ChunkType.CHILD),
        ).values_list("id", flat=True)
    )
    rows = [row for row in rows if row[0] in eligible_ids][: max(1, min(limit, 100))]
    scores = {
        paragraph_id: VectorScore(
            raw=max(-1.0, min(1.0, 1.0 - float(distance))),
            normalized=normalize_vector_score(max(-1.0, min(1.0, 1.0 - float(distance)))),
        )
        for paragraph_id, distance in rows
    }
    elapsed = max(0, perf_counter() - started)
    VECTOR_QUERIES_TOTAL.labels("pgvector", settings.VECTOR_SEARCH_MODE, "success").inc()
    VECTOR_QUERY_DURATION.labels("pgvector", settings.VECTOR_SEARCH_MODE).observe(elapsed)
    VECTOR_CANDIDATES_TOTAL.labels("pgvector").inc(len(rows))
    return PgvectorCandidates(
        scores,
        tuple(paragraph_id for paragraph_id, _ in rows),
        round(elapsed * 1000, 3),
    )
