import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean

from api.models import KnowledgeBase, Paragraph

from .reranker import reranker_capabilities
from .retrieval import (
    prepare_retrieval,
    resolve_retrieval_settings,
    retrieve_from_prepared,
    settings_payload,
)


class EvaluationDataError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    relevant_paragraph_ids: frozenset[int]
    category: str
    difficulty: str
    synthetic: bool


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def dataset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evaluation_cases(path: Path, knowledge_base: KnowledgeBase) -> list[EvaluationCase]:
    if not path.is_file():
        raise EvaluationDataError("评测数据集不存在")
    cases = []
    seen_ids = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise EvaluationDataError(f"第{line_number}行不是合法JSON") from exc
        case_id = str(data.get("id", "")).strip()
        question = str(data.get("question", "")).strip()
        targets = data.get("relevant_targets")
        if not case_id or case_id in seen_ids:
            raise EvaluationDataError(f"第{line_number}行ID为空或重复")
        if not question or len(question) > 1000:
            raise EvaluationDataError(f"第{line_number}行问题为空或过长")
        if not isinstance(targets, list) or not targets:
            raise EvaluationDataError(f"第{line_number}行缺少相关切片")
        relevant_ids = set()
        for target in targets:
            if not isinstance(target, dict):
                raise EvaluationDataError(f"第{line_number}行相关切片格式无效")
            name = str(target.get("document_name", "")).strip()
            position = target.get("position")
            expected_hash = str(target.get("content_sha256", "")).strip().lower()
            matches = list(
                Paragraph.objects.filter(
                    document__knowledge_base=knowledge_base,
                    document__name=name,
                    position=position,
                ).only("id", "content")[:2]
            )
            if len(matches) != 1:
                raise EvaluationDataError(
                    f"{case_id}的目标{name}#{position}不存在或不唯一"
                )
            paragraph = matches[0]
            if not expected_hash or content_sha256(paragraph.content) != expected_hash:
                raise EvaluationDataError(f"{case_id}的目标{name}#{position}内容哈希不匹配")
            relevant_ids.add(paragraph.id)
        seen_ids.add(case_id)
        cases.append(
            EvaluationCase(
                case_id=case_id,
                question=question,
                relevant_paragraph_ids=frozenset(relevant_ids),
                category=str(data.get("category", "未分类"))[:100],
                difficulty=str(data.get("difficulty", "unknown"))[:20],
                synthetic=bool(data.get("synthetic", False)),
            )
        )
    if not cases:
        raise EvaluationDataError("评测数据集为空")
    return cases


def evaluate_ranking(ranked_ids: list[int], relevant_ids: set[int] | frozenset[int]) -> dict:
    hit1 = int(bool(set(ranked_ids[:1]) & set(relevant_ids)))
    hit3 = int(bool(set(ranked_ids[:3]) & set(relevant_ids)))
    recall5 = len(set(ranked_ids[:5]) & set(relevant_ids)) / len(relevant_ids)
    reciprocal_rank = 0.0
    for rank, item_id in enumerate(ranked_ids[:10], start=1):
        if item_id in relevant_ids:
            reciprocal_rank = 1.0 / rank
            break
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item_id in enumerate(ranked_ids[:10], start=1)
        if item_id in relevant_ids
    )
    ideal_count = min(len(relevant_ids), 10)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return {
        "hit_at_1": hit1,
        "hit_at_3": hit3,
        "recall_at_5": recall5,
        "mrr_at_10": reciprocal_rank,
        "ndcg_at_10": dcg / ideal_dcg if ideal_dcg else 0.0,
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def aggregate_strategy(rows: list[dict]) -> dict:
    total = len(rows)
    fallback_count = sum(bool(row["rerank_fallback_code"]) for row in rows)
    return {
        "cases": total,
        "hit_at_1": {"hits": sum(row["hit_at_1"] for row in rows), "total": total},
        "hit_at_3": {"hits": sum(row["hit_at_3"] for row in rows), "total": total},
        "recall_at_5": mean(row["recall_at_5"] for row in rows),
        "mrr_at_10": mean(row["mrr_at_10"] for row in rows),
        "ndcg_at_10": mean(row["ndcg_at_10"] for row in rows),
        "latency_p50_ms": _percentile([row["latency_ms"] for row in rows], 0.50),
        "latency_p95_ms": _percentile([row["latency_ms"] for row in rows], 0.95),
        "reranker_fallback": {"count": fallback_count, "total": total},
    }


def strategy_settings(base, strategy: str):
    strategy = strategy.upper()
    if strategy == "VECTOR":
        return replace(base, mode=KnowledgeBase.RetrievalMode.VECTOR, rerank_enabled=False)
    if strategy == "WEIGHTED":
        return replace(
            base,
            mode=KnowledgeBase.RetrievalMode.HYBRID,
            fusion_method=KnowledgeBase.FusionMethod.WEIGHTED,
            rerank_enabled=False,
        )
    if strategy == "RRF":
        return replace(
            base,
            mode=KnowledgeBase.RetrievalMode.HYBRID,
            fusion_method=KnowledgeBase.FusionMethod.RRF,
            rerank_enabled=False,
        )
    if strategy == "RRF_RERANK":
        return replace(
            base,
            mode=KnowledgeBase.RetrievalMode.HYBRID,
            fusion_method=KnowledgeBase.FusionMethod.RRF,
            rerank_enabled=True,
        )
    raise EvaluationDataError(f"不支持的评测策略：{strategy}")


def run_evaluation(knowledge_base, cases, strategies) -> dict:
    base = resolve_retrieval_settings(knowledge_base)
    resolved_strategies = {
        strategy: strategy_settings(base, strategy) for strategy in strategies
    }
    strategy_rows = {strategy: [] for strategy in strategies}
    for case in cases:
        prepared = prepare_retrieval(knowledge_base, case.question)
        for strategy in strategies:
            result = retrieve_from_prepared(
                prepared,
                settings=resolved_strategies[strategy],
            )
            ranked_ids = result.ranked_candidate_ids(10)
            metrics = evaluate_ranking(ranked_ids, case.relevant_paragraph_ids)
            strategy_rows[strategy].append(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "category": case.category,
                    "difficulty": case.difficulty,
                    "synthetic": case.synthetic,
                    "relevant_paragraph_ids": sorted(case.relevant_paragraph_ids),
                    "ranked_paragraph_ids": ranked_ids,
                    "latency_ms": result.latency_ms,
                    "rerank_applied": result.rerank_applied,
                    "rerank_fallback_code": result.rerank_fallback_code,
                    **metrics,
                }
            )
    return {
        "summary": {
            strategy: aggregate_strategy(rows) for strategy, rows in strategy_rows.items()
        },
        "rows": strategy_rows,
        "strategy_configs": {
            strategy: settings_payload(config)
            for strategy, config in resolved_strategies.items()
        },
        "reranker": reranker_capabilities(),
    }
