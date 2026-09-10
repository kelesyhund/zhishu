from dataclasses import dataclass


@dataclass(frozen=True)
class RrfScore:
    raw: float
    normalized: float
    rank: int


def reciprocal_rank_fusion(
    vector_ranks: dict[int, int],
    keyword_ranks: dict[int, int],
    *,
    rrf_k: int,
) -> dict[int, RrfScore]:
    candidate_ids = set(vector_ranks) | set(keyword_ranks)
    raw_scores = {
        item_id: sum(
            1.0 / (rrf_k + rank)
            for rank in (vector_ranks.get(item_id), keyword_ranks.get(item_id))
            if rank is not None
        )
        for item_id in candidate_ids
    }
    maximum = max(raw_scores.values(), default=0.0)
    infinity = float("inf")
    ordered = sorted(
        candidate_ids,
        key=lambda item_id: (
            -raw_scores[item_id],
            vector_ranks.get(item_id, infinity),
            keyword_ranks.get(item_id, infinity),
            item_id,
        ),
    )
    return {
        item_id: RrfScore(
            raw=raw_scores[item_id],
            normalized=raw_scores[item_id] / maximum if maximum else 0.0,
            rank=rank,
        )
        for rank, item_id in enumerate(ordered, start=1)
    }
