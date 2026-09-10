from dataclasses import dataclass

from .embeddings import cosine_similarity


@dataclass(frozen=True)
class VectorScore:
    raw: float
    normalized: float


def normalize_vector_score(raw_score: float) -> float:
    return max(0.0, min(1.0, (raw_score + 1.0) / 2.0))


def calculate_vector_scores(query_vector, paragraphs) -> dict[int, VectorScore]:
    scores: dict[int, VectorScore] = {}
    for paragraph in paragraphs:
        raw = cosine_similarity(query_vector, paragraph.embedding)
        scores[paragraph.id] = VectorScore(raw=raw, normalized=normalize_vector_score(raw))
    return scores


def rank_vector_scores(scores: dict[int, VectorScore], limit: int) -> dict[int, int]:
    ordered = sorted(scores, key=lambda item_id: (-scores[item_id].normalized, item_id))[:limit]
    return {item_id: rank for rank, item_id in enumerate(ordered, start=1)}
