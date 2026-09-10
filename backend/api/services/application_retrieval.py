from dataclasses import dataclass
from time import perf_counter

from .prompt_builder import format_reference_block
from .retrieval import retrieve_candidates


@dataclass(frozen=True)
class ApplicationRetrievalResult:
    references: list[dict]
    latency_ms: int
    candidate_count: int


def retrieve_application_context(runtime, query):
    started = perf_counter()
    fused = {}
    candidate_count = 0
    for runtime_knowledge in runtime.knowledge_bases:
        knowledge_base = runtime_knowledge.knowledge_base
        result = retrieve_candidates(
            knowledge_base,
            query,
            top_k_override=min(20, max(runtime.global_top_k * 3, runtime.global_top_k)),
        )
        refs = result.references()
        candidate_count += len(refs)
        for rank, reference in enumerate(refs, start=1):
            paragraph_id = reference["paragraph_id"]
            score = runtime_knowledge.weight / (60 + rank)
            existing = fused.get(paragraph_id)
            if existing:
                existing["application_rrf_score"] += score
                continue
            fused[paragraph_id] = {
                **reference,
                "knowledge_base_id": knowledge_base.id,
                "knowledge_base_name": knowledge_base.name,
                "application_rrf_score": score,
            }
    ordered = sorted(
        fused.values(),
        key=lambda item: (-item["application_rrf_score"], item["paragraph_id"]),
    )
    max_score = ordered[0]["application_rrf_score"] if ordered else 1
    selected = []
    context_chars = 0
    for item in ordered:
        if len(selected) >= runtime.global_top_k:
            break
        content = item["content"]
        block = format_reference_block(
            len(selected) + 1,
            item["document_name"],
            item.get("position", 0),
            content,
        )
        separator = 2 if selected else 0
        remaining = runtime.max_context_chars - context_chars - separator
        if remaining <= 0:
            break
        if len(block) > remaining:
            if selected:
                continue
            content = content[: max(0, remaining - 120)]
            block = format_reference_block(1, item["document_name"], item.get("position", 0), content)
            while len(block) > remaining and content:
                content = content[:-1]
                block = format_reference_block(1, item["document_name"], item.get("position", 0), content)
        selected.append(
            {
                **item,
                "content": content,
                "similarity": round(item["application_rrf_score"] / max_score, 6),
                "final_score": round(item["application_rrf_score"] / max_score, 6),
                "final_rank": len(selected) + 1,
            }
        )
        context_chars += separator + len(block)
    return ApplicationRetrievalResult(
        references=selected,
        latency_ms=round((perf_counter() - started) * 1000),
        candidate_count=candidate_count,
    )
