import math
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordScore:
    raw: float
    normalized: float


def calculate_bm25_scores(
    query_tokens: list[str],
    document_tokens: dict[int, list[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[int, KeywordScore]:
    """计算候选集合内的 BM25，并按本次最大值归一化到 0—1。"""

    if not document_tokens:
        return {}
    unique_query_tokens = list(dict.fromkeys(query_tokens))
    document_count = len(document_tokens)
    average_length = sum(len(tokens) for tokens in document_tokens.values()) / document_count
    if not unique_query_tokens or average_length == 0:
        return {document_id: KeywordScore(0.0, 0.0) for document_id in document_tokens}

    document_token_sets = {document_id: set(tokens) for document_id, tokens in document_tokens.items()}
    document_frequency = {
        token: sum(token in tokens for tokens in document_token_sets.values())
        for token in unique_query_tokens
    }
    raw_scores: dict[int, float] = {}
    for document_id, tokens in document_tokens.items():
        frequencies = Counter(tokens)
        length_factor = 1 - b + b * len(tokens) / average_length
        score = 0.0
        for token in unique_query_tokens:
            term_frequency = frequencies[token]
            if not term_frequency:
                continue
            frequency = document_frequency[token]
            inverse_document_frequency = math.log(
                1 + (document_count - frequency + 0.5) / (frequency + 0.5)
            )
            score += inverse_document_frequency * (
                term_frequency * (k1 + 1)
                / (term_frequency + k1 * length_factor)
            )
        raw_scores[document_id] = score

    maximum = max(raw_scores.values(), default=0.0)
    return {
        document_id: KeywordScore(raw, raw / maximum if maximum > 0 else 0.0)
        for document_id, raw in raw_scores.items()
    }


def rank_keyword_scores(
    scores: dict[int, KeywordScore],
    limit: int,
    *,
    vector_scores=None,
) -> dict[int, int]:
    def secondary_score(item_id: int) -> float:
        if not vector_scores or item_id not in vector_scores:
            return 0.0
        return vector_scores[item_id].normalized

    ordered = sorted(
        (item_id for item_id, score in scores.items() if score.raw > 0),
        key=lambda item_id: (
            -scores[item_id].normalized,
            -secondary_score(item_id),
            item_id,
        ),
    )[:limit]
    return {item_id: rank for rank, item_id in enumerate(ordered, start=1)}
