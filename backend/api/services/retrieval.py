from dataclasses import dataclass, replace
from time import perf_counter

from api.models import KnowledgeBase, Paragraph

from .embeddings import embed_texts
from .keyword_retrieval import KeywordScore, calculate_bm25_scores, rank_keyword_scores
from .model_clients import ModelServiceError
from .model_resolution import active_embedding_signature
from .prompt_builder import format_reference_block
from .reranker import rerank_candidates
from .retrieval_tokenizer import tokenize_text
from .rrf import reciprocal_rank_fusion
from .vector_retrieval import (
    VectorScore,
    calculate_vector_scores,
    normalize_vector_score,
    rank_vector_scores,
)
from ..observability import (
    RAG_BM25_DURATION,
    RAG_CANDIDATES_TOTAL,
    RAG_RERANK_DURATION,
    RAG_RERANK_FALLBACKS_TOTAL,
    RAG_RETRIEVAL_DURATION,
    RAG_VECTOR_DURATION,
    traced,
)


@dataclass(frozen=True)
class RetrievalSettings:
    mode: str
    fusion_method: str
    top_k: int
    threshold: float
    vector_weight: float
    keyword_weight: float
    vector_candidate_k: int
    keyword_candidate_k: int
    rrf_k: int
    rerank_enabled: bool
    rerank_candidate_k: int
    max_context_chars: int


@dataclass
class RetrievalCandidate:
    paragraph_id: int
    document_id: int
    document_name: str
    document_source_id: str
    document_sha256: str
    position: int
    content: str
    parent_id: int | None
    parent_position: int | None
    parent_content: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    source_block_ids: list[str]
    vector_score_raw: float
    vector_score_normalized: float
    keyword_score_raw: float
    keyword_score: float
    weighted_score: float = 0.0
    vector_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score_raw: float | None = None
    rrf_score_normalized: float | None = None
    rrf_rank: int | None = None
    pre_rerank_rank: int | None = None
    rerank_score: float | None = None
    rerank_rank: int | None = None
    final_rank: int | None = None
    final_score: float = 0.0
    included: bool = False
    exclusion_reason: str = ""
    context_content: str = ""
    content_truncated: bool = False


@dataclass(frozen=True)
class PreparedRetrieval:
    query: str
    settings: RetrievalSettings
    candidates: tuple[RetrievalCandidate, ...]
    preparation_ms: int


@dataclass
class RetrievalResult:
    query: str
    settings: RetrievalSettings
    candidates: list[RetrievalCandidate]
    candidate_count: int
    selected_count: int
    context_chars: int
    latency_ms: int
    stage_timings: dict[str, int]
    rerank_applied: bool = False
    rerank_fallback_code: str = ""
    rerank_fallback_reason: str = ""

    def references(self) -> list[dict]:
        return [
            {
                "paragraph_id": item.paragraph_id,
                "document_id": item.document_id,
                "document_name": item.document_name,
                "document_source_id": item.document_source_id,
                "document_sha256": item.document_sha256,
                "position": item.position,
                "content": item.context_content,
                "matched_content": item.content,
                "parent_id": item.parent_id,
                "parent_position": item.parent_position,
                "heading_path": item.heading_path,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "source_block_ids": item.source_block_ids,
                "similarity": round(item.final_score, 6),
                "vector_score_raw": round(item.vector_score_raw, 6),
                "vector_score_normalized": round(item.vector_score_normalized, 6),
                "keyword_score": round(item.keyword_score, 6),
                "final_score": round(item.final_score, 6),
                "fusion_method": self.settings.fusion_method,
                "vector_rank": item.vector_rank,
                "keyword_rank": item.keyword_rank,
                "rrf_rank": item.rrf_rank,
                "rerank_rank": item.rerank_rank,
                "final_rank": item.final_rank,
            }
            for item in self.candidates
            if item.included
        ]

    def ranked_candidate_ids(self, limit: int = 10) -> list[int]:
        ranked = sorted(
            (item for item in self.candidates if item.final_rank is not None),
            key=lambda item: item.final_rank or 0,
        )
        return [item.paragraph_id for item in ranked[:limit]]

    def debug_payload(self, candidate_limit: int) -> dict:
        return {
            "query": self.query,
            "effective_config": settings_payload(self.settings),
            "items": [candidate_payload(item) for item in self.candidates[:candidate_limit]],
            "selected_count": self.selected_count,
            "candidate_count": self.candidate_count,
            "context_chars": self.context_chars,
            "latency_ms": self.latency_ms,
            "stage_timings": self.stage_timings,
            "rerank_applied": self.rerank_applied,
            "rerank_fallback_code": self.rerank_fallback_code,
            "rerank_fallback_reason": self.rerank_fallback_reason,
        }


def candidate_payload(item: RetrievalCandidate) -> dict:
    def rounded(value):
        return round(value, 6) if value is not None else None

    return {
        "paragraph_id": item.paragraph_id,
        "document_id": item.document_id,
        "document_name": item.document_name,
        "document_source_id": item.document_source_id,
        "document_sha256": item.document_sha256,
        "position": item.position,
        "content": item.content,
        "parent_id": item.parent_id,
        "parent_position": item.parent_position,
        "parent_content": item.parent_content,
        "heading_path": item.heading_path,
        "page_start": item.page_start,
        "page_end": item.page_end,
        "source_block_ids": item.source_block_ids,
        "vector_score_raw": rounded(item.vector_score_raw),
        "vector_score_normalized": rounded(item.vector_score_normalized),
        "vector_rank": item.vector_rank,
        "keyword_score_raw": rounded(item.keyword_score_raw),
        "keyword_score": rounded(item.keyword_score),
        "keyword_rank": item.keyword_rank,
        "weighted_score": rounded(item.weighted_score),
        "rrf_score_raw": rounded(item.rrf_score_raw),
        "rrf_score_normalized": rounded(item.rrf_score_normalized),
        "rrf_rank": item.rrf_rank,
        "pre_rerank_rank": item.pre_rerank_rank,
        "rerank_score": rounded(item.rerank_score),
        "rerank_rank": item.rerank_rank,
        "final_rank": item.final_rank,
        "final_score": rounded(item.final_score),
        "included": item.included,
        "content_truncated": item.content_truncated,
        "exclusion_reason": item.exclusion_reason,
    }


def settings_payload(settings: RetrievalSettings) -> dict:
    return {
        "mode": settings.mode,
        "fusion_method": settings.fusion_method,
        "top_k": settings.top_k,
        "threshold": settings.threshold,
        "vector_weight": settings.vector_weight,
        "keyword_weight": settings.keyword_weight,
        "vector_candidate_k": settings.vector_candidate_k,
        "keyword_candidate_k": settings.keyword_candidate_k,
        "rrf_k": settings.rrf_k,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_candidate_k": settings.rerank_candidate_k,
        "max_context_chars": settings.max_context_chars,
    }


def resolve_retrieval_settings(knowledge_base: KnowledgeBase) -> RetrievalSettings:
    return RetrievalSettings(
        mode=knowledge_base.retrieval_mode,
        fusion_method=knowledge_base.fusion_method,
        top_k=knowledge_base.retrieval_top_k,
        threshold=knowledge_base.similarity_threshold,
        vector_weight=knowledge_base.vector_weight,
        keyword_weight=1 - knowledge_base.vector_weight,
        vector_candidate_k=knowledge_base.vector_candidate_k,
        keyword_candidate_k=knowledge_base.keyword_candidate_k,
        rrf_k=knowledge_base.rrf_k,
        rerank_enabled=knowledge_base.rerank_enabled,
        rerank_candidate_k=knowledge_base.rerank_candidate_k,
        max_context_chars=knowledge_base.max_context_chars,
    )


def prepare_retrieval(knowledge_base, query, *, embedding_function=None) -> PreparedRetrieval:
    started = perf_counter()
    settings = resolve_retrieval_settings(knowledge_base)
    paragraphs = Paragraph.objects.filter(
        document__knowledge_base=knowledge_base,
        document__status="SUCCESS",
        chunk_type__in=(Paragraph.ChunkType.LEGACY, Paragraph.ChunkType.CHILD),
    ).select_related("document", "parent_section")
    signature = active_embedding_signature(knowledge_base)
    if signature:
        paragraphs = paragraphs.filter(document__embedding_signature=signature)
    paragraph_list = list(paragraphs)
    if not paragraph_list:
        return PreparedRetrieval(query, settings, (), round((perf_counter() - started) * 1000))

    with traced("query.embedding"):
        query_vector = (embedding_function or embed_texts)([query], knowledge_base)[0]
    vector_started = perf_counter()
    try:
        with traced("retrieval.vector", candidate_count=len(paragraph_list)):
            vector_scores = calculate_vector_scores(query_vector, paragraph_list)
    except ValueError as exc:
        raise ModelServiceError(
            "文档向量与当前Embedding配置不兼容，请重新处理文档",
            "VECTOR_MISMATCH",
        ) from exc
    finally:
        RAG_VECTOR_DURATION.observe(max(0, perf_counter() - vector_started))
    bm25_started = perf_counter()
    with traced("retrieval.bm25", candidate_count=len(paragraph_list)):
        tokenized = {paragraph.id: tokenize_text(paragraph.content) for paragraph in paragraph_list}
        keyword_scores = calculate_bm25_scores(tokenize_text(query), tokenized)
    RAG_BM25_DURATION.observe(max(0, perf_counter() - bm25_started))
    candidates = tuple(
        RetrievalCandidate(
            paragraph_id=paragraph.id,
            document_id=paragraph.document_id,
            document_name=paragraph.document.name,
            document_source_id=paragraph.document.source_id,
            document_sha256=paragraph.document.source_sha256,
            position=paragraph.position,
            content=paragraph.content,
            parent_id=paragraph.parent_section_id,
            parent_position=(
                paragraph.parent_section.position if paragraph.parent_section_id else None
            ),
            parent_content=(
                paragraph.parent_section.content if paragraph.parent_section_id else ""
            ),
            heading_path=list(paragraph.heading_path),
            page_start=paragraph.page_start,
            page_end=paragraph.page_end,
            source_block_ids=list(paragraph.source_block_ids),
            vector_score_raw=vector_scores[paragraph.id].raw,
            vector_score_normalized=vector_scores[paragraph.id].normalized,
            keyword_score_raw=keyword_scores[paragraph.id].raw,
            keyword_score=keyword_scores[paragraph.id].normalized,
        )
        for paragraph in paragraph_list
    )
    return PreparedRetrieval(
        query,
        settings,
        candidates,
        round((perf_counter() - started) * 1000),
    )


def _fit_reference(candidate, budget, number):
    empty = format_reference_block(number, candidate.document_name, candidate.position, "")
    source_content = candidate.parent_content or candidate.content
    child_block = format_reference_block(
        number, candidate.document_name, candidate.position, candidate.content
    )
    if candidate.parent_content and len(child_block) <= budget:
        return candidate.content, len(child_block), True
    content = source_content[: max(0, budget - len(empty))]
    block = format_reference_block(number, candidate.document_name, candidate.position, content)
    while len(block) > budget and content:
        content = content[:-1]
        block = format_reference_block(number, candidate.document_name, candidate.position, content)
    return content, len(block), content != source_content


def retrieve_from_prepared(
    prepared,
    *,
    settings=None,
    top_k_override=None,
    reranker_function=None,
) -> RetrievalResult:
    ranking_started = perf_counter()
    effective = settings or prepared.settings
    if top_k_override is not None:
        effective = replace(effective, top_k=max(1, min(top_k_override, 20)))
    candidates = [replace(item) for item in prepared.candidates]
    if not candidates:
        return RetrievalResult(
            prepared.query, effective, [], 0, 0, 0, prepared.preparation_ms,
            {"preparation_ms": prepared.preparation_ms, "ranking_ms": 0, "rerank_ms": 0},
        )

    by_id = {item.paragraph_id: item for item in candidates}
    vector_scores = {
        item.paragraph_id: VectorScore(item.vector_score_raw, item.vector_score_normalized)
        for item in candidates
    }
    keyword_scores = {
        item.paragraph_id: KeywordScore(item.keyword_score_raw, item.keyword_score)
        for item in candidates
    }
    vector_ranks = rank_vector_scores(vector_scores, effective.vector_candidate_k)
    keyword_ranks = rank_keyword_scores(
        keyword_scores,
        effective.keyword_candidate_k,
        vector_scores=vector_scores,
    )
    for item in candidates:
        item.vector_rank = vector_ranks.get(item.paragraph_id)
        item.keyword_rank = keyword_ranks.get(item.paragraph_id)
        item.weighted_score = (
            effective.vector_weight * item.vector_score_normalized
            + effective.keyword_weight * item.keyword_score
        )

    uses_rrf = (
        effective.mode == KnowledgeBase.RetrievalMode.HYBRID
        and effective.fusion_method == KnowledgeBase.FusionMethod.RRF
    )
    if effective.mode == KnowledgeBase.RetrievalMode.VECTOR:
        ordered = sorted(candidates, key=lambda item: (-item.vector_score_normalized, item.paragraph_id))
        for item in ordered:
            item.final_score = item.vector_score_normalized
    elif not uses_rrf:
        ordered = sorted(
            candidates,
            key=lambda item: (-item.weighted_score, -item.vector_score_normalized, item.paragraph_id),
        )
        for item in ordered:
            item.final_score = item.weighted_score
    else:
        rrf_scores = reciprocal_rank_fusion(vector_ranks, keyword_ranks, rrf_k=effective.rrf_k)
        ordered = []
        for item_id, score in sorted(rrf_scores.items(), key=lambda pair: pair[1].rank):
            item = by_id[item_id]
            item.rrf_score_raw = score.raw
            item.rrf_score_normalized = score.normalized
            item.rrf_rank = score.rank
            item.final_score = score.normalized
            ordered.append(item)

    eligible, rejected = [], []
    for item in ordered:
        if item.final_score < effective.threshold:
            item.exclusion_reason = "低于相关度阈值"
            rejected.append(item)
        else:
            eligible.append(item)
    for rank, item in enumerate(eligible, start=1):
        item.pre_rerank_rank = rank

    rerank_started = perf_counter()
    rerank_applied = False
    fallback_code = fallback_reason = ""
    if effective.rerank_enabled and uses_rrf and eligible:
        rerank_input = eligible[: effective.rerank_candidate_k]
        with traced("retrieval.rerank", candidate_count=len(rerank_input)):
            outcome = (reranker_function or rerank_candidates)(prepared.query, rerank_input)
        rerank_applied = outcome.applied
        fallback_code = outcome.fallback_code
        fallback_reason = outcome.fallback_reason
        if outcome.applied:
            input_by_id = {item.paragraph_id: item for item in rerank_input}
            reranked = [input_by_id[item_id] for item_id in outcome.ordered_ids]
            for rank, item in enumerate(reranked, start=1):
                item.rerank_score = outcome.scores[item.paragraph_id]
                item.rerank_rank = rank
            eligible = reranked + eligible[len(rerank_input):]
    rerank_ms = round((perf_counter() - rerank_started) * 1000)

    for rank, item in enumerate(eligible, start=1):
        item.final_rank = rank
    final_order = eligible + rejected
    selected_count = context_chars = 0
    selected_parent_keys = set()
    for candidate in final_order:
        if candidate.final_rank is None:
            continue
        if selected_count >= effective.top_k:
            candidate.exclusion_reason = "超过返回数量限制"
            continue
        parent_key = (
            (candidate.document_id, candidate.parent_id)
            if candidate.parent_id
            else (candidate.document_id, "child", candidate.paragraph_id)
        )
        if parent_key in selected_parent_keys:
            candidate.exclusion_reason = "对应父切片已进入上下文"
            continue
        context_source = candidate.parent_content or candidate.content
        number = selected_count + 1
        block = format_reference_block(number, candidate.document_name, candidate.position, context_source)
        if selected_count == 0 and len(block) > effective.max_context_chars:
            content, block_length, truncated = _fit_reference(
                candidate, effective.max_context_chars, number
            )
            candidate.context_content = content
            candidate.content_truncated = truncated
            candidate.included = True
            selected_parent_keys.add(parent_key)
            selected_count = 1
            context_chars = block_length
            continue
        separator_length = 2 if selected_count else 0
        if context_chars + separator_length + len(block) > effective.max_context_chars:
            remaining = effective.max_context_chars - context_chars - separator_length
            child_block = format_reference_block(
                number, candidate.document_name, candidate.position, candidate.content
            )
            if candidate.parent_content and len(child_block) <= remaining:
                context_source = candidate.content
                block = child_block
                candidate.content_truncated = True
            else:
                candidate.exclusion_reason = "超过上下文字符预算"
                continue
        candidate.context_content = context_source
        candidate.included = True
        selected_parent_keys.add(parent_key)
        selected_count += 1
        context_chars += separator_length + len(block)

    ranking_ms = round((perf_counter() - ranking_started) * 1000)
    return RetrievalResult(
        query=prepared.query,
        settings=effective,
        candidates=final_order,
        candidate_count=len(final_order),
        selected_count=selected_count,
        context_chars=context_chars,
        latency_ms=prepared.preparation_ms + ranking_ms,
        stage_timings={
            "preparation_ms": prepared.preparation_ms,
            "ranking_ms": max(0, ranking_ms - rerank_ms),
            "rerank_ms": rerank_ms,
        },
        rerank_applied=rerank_applied,
        rerank_fallback_code=fallback_code,
        rerank_fallback_reason=fallback_reason,
    )


def retrieve_candidates(
    knowledge_base,
    query,
    *,
    top_k_override=None,
    embedding_function=None,
    settings_override=None,
    reranker_function=None,
) -> RetrievalResult:
    started = perf_counter()
    with traced("rag.prepare", knowledge_base_id=knowledge_base.id):
        prepared = prepare_retrieval(knowledge_base, query, embedding_function=embedding_function)
    uses_rrf = (
        prepared.settings.mode == KnowledgeBase.RetrievalMode.HYBRID
        and prepared.settings.fusion_method == KnowledgeBase.FusionMethod.RRF
    )
    with traced("retrieval.rrf" if uses_rrf else "retrieval.rank", candidate_count=len(prepared.candidates)):
        result = retrieve_from_prepared(
            prepared,
            settings=settings_override,
            top_k_override=top_k_override,
            reranker_function=reranker_function,
        )
    strategy = (
        "VECTOR" if result.settings.mode == KnowledgeBase.RetrievalMode.VECTOR
        else result.settings.fusion_method
    )
    RAG_RETRIEVAL_DURATION.labels(strategy).observe(max(0, perf_counter() - started))
    RAG_CANDIDATES_TOTAL.labels(strategy).inc(result.candidate_count)
    RAG_RERANK_DURATION.observe(max(0, result.stage_timings.get("rerank_ms", 0) / 1000))
    if result.rerank_fallback_code:
        code = result.rerank_fallback_code[:40].upper().replace("-", "_")
        RAG_RERANK_FALLBACKS_TOTAL.labels(code if code else "OTHER").inc()
    return result
