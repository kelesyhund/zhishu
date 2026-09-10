import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean

from api.models import KnowledgeBase, Paragraph

from .reranker import reranker_capabilities
from .retrieval import (
    RetrievalCandidate,
    prepare_retrieval,
    resolve_retrieval_settings,
    retrieve_from_prepared,
    settings_payload,
)
from .retrieval_evaluation import EvaluationDataError, strategy_settings


ALLOWED_CATEGORIES = {
    "exact_keyword",
    "semantic_paraphrase",
    "procedure",
    "multi_document",
    "table_parameter",
    "version_difference",
    "no_answer",
}
ALLOWED_REVIEW_STATUSES = {"AUTO_DRAFT", "HUMAN_APPROVED"}


@dataclass(frozen=True)
class StableEvidence:
    source_id: str
    document_sha256: str
    source_block_ids: tuple[str, ...]
    relevance: int

    @property
    def evidence_id(self) -> str:
        payload = "|".join(
            (self.source_id, self.document_sha256, *sorted(self.source_block_ids))
        )
        return f"evi_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class Stage11EvaluationCase:
    case_id: str
    query: str
    category: str
    split: str
    answerable: bool
    gold_evidence: tuple[StableEvidence, ...]
    answer_key_points: tuple[str, ...]
    review_status: str
    reviewed_at: str
    notes: str


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def queries_are_near_duplicates(left: str, right: str) -> bool:
    normalized_left = normalize_query(left)
    normalized_right = normalize_query(right)
    if normalized_left == normalized_right:
        return True
    if min(len(normalized_left), len(normalized_right)) < 12:
        return False
    return SequenceMatcher(None, normalized_left, normalized_right).ratio() >= 0.94


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise EvaluationDataError(f"文件不存在：{path.name}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationDataError(f"第{line_number}行不是合法JSON") from exc
        if not isinstance(row, dict):
            raise EvaluationDataError(f"第{line_number}行必须是JSON对象")
        rows.append(row)
    if not rows:
        raise EvaluationDataError("文件内容为空")
    return rows


def load_corpus_manifest(path: Path) -> dict[str, dict]:
    entries = read_jsonl(path)
    by_source = {}
    for index, entry in enumerate(entries, start=1):
        source_id = str(entry.get("source_id", "")).strip()
        required = ("title", "source_url", "publisher", "license", "retrieved_at", "sha256")
        if not source_id or source_id in by_source:
            raise EvaluationDataError(f"Manifest第{index}行source_id为空或重复")
        if any(not str(entry.get(field, "")).strip() for field in required):
            raise EvaluationDataError(f"Manifest第{index}行缺少来源、许可、日期或哈希")
        digest = str(entry["sha256"]).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvaluationDataError(f"Manifest第{index}行sha256无效")
        local_path = str(entry.get("local_path", "")).strip()
        if entry.get("included", True) and local_path:
            candidate = (path.parent / local_path).resolve()
            if candidate.is_file() and file_sha256(candidate) != digest:
                raise EvaluationDataError(f"Manifest中的{source_id}本地文件哈希不匹配")
        by_source[source_id] = entry
    return by_source


def _parse_evidence(value, case_id: str, answerable: bool) -> tuple[StableEvidence, ...]:
    if not isinstance(value, list):
        raise EvaluationDataError(f"{case_id}的gold_evidence必须是数组")
    evidence = []
    seen = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise EvaluationDataError(f"{case_id}的第{index}条证据格式无效")
        source_id = str(raw.get("source_id", "")).strip()
        document_sha256 = str(raw.get("document_sha256", "")).strip().lower()
        block_ids = raw.get("source_block_ids")
        relevance = raw.get("relevance")
        if not source_id or not re.fullmatch(r"[0-9a-f]{64}", document_sha256):
            raise EvaluationDataError(f"{case_id}的第{index}条证据来源或文档哈希无效")
        if not isinstance(block_ids, list) or not block_ids or any(
            not isinstance(item, str) or not item.strip() for item in block_ids
        ):
            raise EvaluationDataError(f"{case_id}的第{index}条证据缺少稳定Block ID")
        if isinstance(relevance, bool) or relevance not in (0, 1, 2):
            raise EvaluationDataError(f"{case_id}的第{index}条证据相关度必须是0、1或2")
        item = StableEvidence(
            source_id=source_id,
            document_sha256=document_sha256,
            source_block_ids=tuple(dict.fromkeys(item.strip() for item in block_ids)),
            relevance=relevance,
        )
        if item.evidence_id in seen:
            raise EvaluationDataError(f"{case_id}包含重复Gold Evidence")
        seen.add(item.evidence_id)
        evidence.append(item)
    if answerable and not any(item.relevance > 0 for item in evidence):
        raise EvaluationDataError(f"{case_id}是有答案问题但缺少相关Gold Evidence")
    if not answerable and evidence:
        raise EvaluationDataError(f"{case_id}是无答案问题，gold_evidence必须为空")
    return tuple(evidence)


def load_stage11_cases(
    path: Path,
    *,
    manifest_path: Path | None = None,
    knowledge_base: KnowledgeBase | None = None,
    require_approved: bool = False,
    expected_split: str | None = None,
) -> list[Stage11EvaluationCase]:
    manifest = load_corpus_manifest(manifest_path) if manifest_path else None
    cases = []
    seen_ids = set()
    seen_queries: list[tuple[str, str]] = []
    for line_number, data in enumerate(read_jsonl(path), start=1):
        case_id = str(data.get("case_id", "")).strip()
        query = str(data.get("query", "")).strip()
        category = str(data.get("category", "")).strip()
        split = str(data.get("split", "")).strip().lower()
        review_status = str(data.get("review_status", "")).strip().upper()
        answerable = data.get("answerable")
        if not case_id or case_id in seen_ids:
            raise EvaluationDataError(f"第{line_number}行case_id为空或重复")
        if not query or len(query) > 1000:
            raise EvaluationDataError(f"{case_id}的问题为空或过长")
        if not isinstance(answerable, bool):
            raise EvaluationDataError(f"{case_id}的answerable必须是布尔值")
        if category not in ALLOWED_CATEGORIES:
            raise EvaluationDataError(f"{case_id}的问题分类无效：{category}")
        if split not in {"dev", "test"}:
            raise EvaluationDataError(f"{case_id}的split必须是dev或test")
        if expected_split and split != expected_split:
            raise EvaluationDataError(f"{case_id}不属于{expected_split}集合")
        if review_status not in ALLOWED_REVIEW_STATUSES:
            raise EvaluationDataError(f"{case_id}的review_status无效")
        if require_approved and review_status != "HUMAN_APPROVED":
            raise EvaluationDataError(f"{case_id}尚未由用户人工确认")
        reviewed_at = str(data.get("reviewed_at", "")).strip()
        if review_status == "HUMAN_APPROVED" and not reviewed_at:
            raise EvaluationDataError(f"{case_id}已确认但缺少reviewed_at")
        for previous_id, previous_query in seen_queries:
            if queries_are_near_duplicates(query, previous_query):
                raise EvaluationDataError(f"{case_id}与{previous_id}问题重复或高度近似")
        evidence = _parse_evidence(data.get("gold_evidence"), case_id, answerable)
        key_points = data.get("answer_key_points", [])
        if not isinstance(key_points, list) or any(
            not isinstance(item, str) or not item.strip() for item in key_points
        ):
            raise EvaluationDataError(f"{case_id}的answer_key_points格式无效")
        if answerable and not key_points:
            raise EvaluationDataError(f"{case_id}是有答案问题但缺少答案关键点")
        if not answerable and key_points:
            raise EvaluationDataError(f"{case_id}是无答案问题，答案关键点必须为空")
        if manifest is not None:
            for item in evidence:
                entry = manifest.get(item.source_id)
                if not entry:
                    raise EvaluationDataError(f"{case_id}引用了Manifest中不存在的{item.source_id}")
                if str(entry["sha256"]).lower() != item.document_sha256:
                    raise EvaluationDataError(f"{case_id}的文档哈希与Manifest不一致")
        if knowledge_base is not None:
            for item in evidence:
                exists = Paragraph.objects.filter(
                    document__knowledge_base=knowledge_base,
                    document__source_id=item.source_id,
                    document__source_sha256=item.document_sha256,
                    source_block_ids__isnull=False,
                )
                if not any(
                    set(paragraph.source_block_ids) & set(item.source_block_ids)
                    for paragraph in exists.only("source_block_ids")
                ):
                    raise EvaluationDataError(
                        f"{case_id}的Gold Evidence未映射到当前知识库切片"
                    )
        seen_ids.add(case_id)
        seen_queries.append((case_id, query))
        cases.append(
            Stage11EvaluationCase(
                case_id=case_id,
                query=query,
                category=category,
                split=split,
                answerable=answerable,
                gold_evidence=evidence,
                answer_key_points=tuple(item.strip() for item in key_points),
                review_status=review_status,
                reviewed_at=reviewed_at,
                notes=str(data.get("notes", ""))[:1000],
            )
        )
    return cases


def assert_no_cross_dataset_duplicates(
    left: list[Stage11EvaluationCase], right: list[Stage11EvaluationCase]
):
    for left_case in left:
        for right_case in right:
            if left_case.case_id == right_case.case_id or queries_are_near_duplicates(
                left_case.query, right_case.query
            ):
                raise EvaluationDataError(
                    f"Dev/Test泄漏：{left_case.case_id}与{right_case.case_id}重复或高度近似"
                )


def _matching_evidence(candidate: RetrievalCandidate, case: Stage11EvaluationCase) -> list[int]:
    candidate_blocks = set(candidate.source_block_ids)
    return [
        index
        for index, evidence in enumerate(case.gold_evidence)
        if evidence.relevance > 0
        and candidate.document_source_id == evidence.source_id
        and candidate.document_sha256 == evidence.document_sha256
        and bool(candidate_blocks & set(evidence.source_block_ids))
    ]


def evaluate_stable_ranking(
    ranked_candidates: list[RetrievalCandidate], case: Stage11EvaluationCase
) -> dict:
    if not case.answerable:
        return {
            "hit_at_1": None,
            "hit_at_3": None,
            "recall_at_5": None,
            "recall_at_5_hits": 0,
            "recall_at_5_total": 0,
            "mrr_at_10": None,
            "ndcg_at_10": None,
            "matched_evidence_ids": [],
        }
    relevant = [item for item in case.gold_evidence if item.relevance > 0]
    matches_by_rank = [_matching_evidence(candidate, case) for candidate in ranked_candidates[:10]]
    matched_top5 = {index for matches in matches_by_rank[:5] for index in matches}
    hit1 = int(bool(matches_by_rank[:1] and matches_by_rank[0]))
    hit3 = int(any(matches_by_rank[:3]))
    first_relevant = next((rank for rank, matches in enumerate(matches_by_rank, 1) if matches), None)

    claimed = set()
    gains = []
    for matches in matches_by_rank:
        new_matches = [index for index in matches if index not in claimed]
        grade = max((case.gold_evidence[index].relevance for index in new_matches), default=0)
        if grade:
            claimed.update(new_matches)
        gains.append(grade)
    dcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(gains, 1))
    ideal_grades = sorted((item.relevance for item in relevant), reverse=True)[:10]
    ideal_dcg = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, 1)
    )
    return {
        "hit_at_1": hit1,
        "hit_at_3": hit3,
        "recall_at_5": len(matched_top5) / len(relevant),
        "recall_at_5_hits": len(matched_top5),
        "recall_at_5_total": len(relevant),
        "mrr_at_10": 1 / first_relevant if first_relevant else 0.0,
        "ndcg_at_10": dcg / ideal_dcg if ideal_dcg else 0.0,
        "matched_evidence_ids": [case.gold_evidence[index].evidence_id for index in sorted(claimed)],
    }


def percentile(values: list[int], value: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(value * len(ordered)) - 1)]


def aggregate_stage11_rows(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["answerable"]]
    total = len(rows)
    recall_hits = sum(row["recall_at_5_hits"] for row in scored)
    recall_total = sum(row["recall_at_5_total"] for row in scored)
    fallback_count = sum(bool(row["rerank_fallback_code"]) for row in rows)
    applied_count = sum(bool(row["rerank_applied"]) for row in rows)
    return {
        "cases": total,
        "answerable_cases": len(scored),
        "hit_at_1": {"hits": sum(row["hit_at_1"] for row in scored), "total": len(scored)},
        "hit_at_3": {"hits": sum(row["hit_at_3"] for row in scored), "total": len(scored)},
        "recall_at_5": {
            "hits": recall_hits,
            "total": recall_total,
            "value": recall_hits / recall_total if recall_total else 0.0,
        },
        "mrr_at_10": mean(row["mrr_at_10"] for row in scored) if scored else 0.0,
        "ndcg_at_10": mean(row["ndcg_at_10"] for row in scored) if scored else 0.0,
        "latency_p50_ms": percentile([row["latency_ms"] for row in rows], 0.50),
        "latency_p95_ms": percentile([row["latency_ms"] for row in rows], 0.95),
        "reranker": {
            "applied": applied_count,
            "fallback": fallback_count,
            "total": total,
            "success_rate": applied_count / total if total else 0.0,
        },
    }


def run_stage11_retrieval_evaluation(knowledge_base, cases, strategies) -> dict:
    base = resolve_retrieval_settings(knowledge_base)
    resolved = {strategy: strategy_settings(base, strategy) for strategy in strategies}
    rows_by_strategy = {strategy: [] for strategy in strategies}
    for case in cases:
        prepared = prepare_retrieval(knowledge_base, case.query)
        for strategy in strategies:
            result = retrieve_from_prepared(prepared, settings=resolved[strategy])
            ranked = sorted(
                (item for item in result.candidates if item.final_rank is not None),
                key=lambda item: item.final_rank or 0,
            )[:10]
            metrics = evaluate_stable_ranking(ranked, case)
            rows_by_strategy[strategy].append(
                {
                    "case_id": case.case_id,
                    "query": case.query,
                    "category": case.category,
                    "split": case.split,
                    "answerable": case.answerable,
                    "review_status": case.review_status,
                    "ranked_paragraph_ids": [item.paragraph_id for item in ranked],
                    "ranked_stable_evidence": [
                        {
                            "paragraph_id": item.paragraph_id,
                            "source_id": item.document_source_id,
                            "document_sha256": item.document_sha256,
                            "source_block_ids": item.source_block_ids,
                        }
                        for item in ranked
                    ],
                    "latency_ms": result.latency_ms,
                    "rerank_applied": result.rerank_applied,
                    "rerank_fallback_code": result.rerank_fallback_code,
                    **metrics,
                }
            )
    summary = {}
    category_summary = {}
    for strategy, rows in rows_by_strategy.items():
        summary[strategy] = aggregate_stage11_rows(rows)
        categories = sorted({row["category"] for row in rows})
        category_summary[strategy] = {
            category: aggregate_stage11_rows([row for row in rows if row["category"] == category])
            for category in categories
        }
    return {
        "summary": summary,
        "category_summary": category_summary,
        "rows": rows_by_strategy,
        "strategy_configs": {
            strategy: settings_payload(config) for strategy, config in resolved.items()
        },
        "reranker": reranker_capabilities(),
    }


def _citation_matches_evidence(citation: dict, evidence: StableEvidence) -> bool:
    return (
        str(citation.get("source_id", "")) == evidence.source_id
        and str(citation.get("document_sha256", "")).lower() == evidence.document_sha256
        and bool(set(citation.get("source_block_ids") or ()) & set(evidence.source_block_ids))
    )


def evaluate_answer_record(case: Stage11EvaluationCase, record: dict) -> dict:
    citations = record.get("citations", [])
    if not isinstance(citations, list):
        raise EvaluationDataError(f"{case.case_id}的citations必须是数组")
    relevant_evidence = [item for item in case.gold_evidence if item.relevance > 0]
    supported_citations = sum(
        any(_citation_matches_evidence(citation, evidence) for evidence in relevant_evidence)
        for citation in citations
    )
    cited_evidence = {
        evidence.evidence_id
        for evidence in relevant_evidence
        if any(_citation_matches_evidence(citation, evidence) for citation in citations)
    }
    judgments = record.get("claim_judgments")
    faithfulness = None
    faithfulness_supported = faithfulness_total = 0
    judge_failed = False
    if judgments is not None:
        if not isinstance(judgments, list) or not judgments:
            judge_failed = True
        else:
            statuses = [
                item.get("status") if isinstance(item, dict) else None for item in judgments
            ]
            if any(status not in {"SUPPORTED", "UNSUPPORTED"} for status in statuses):
                judge_failed = True
            else:
                faithfulness_total = len(statuses)
                faithfulness_supported = statuses.count("SUPPORTED")
                faithfulness = faithfulness_supported / faithfulness_total
    else:
        judge_failed = True
    refused = record.get("refused")
    if not isinstance(refused, bool):
        raise EvaluationDataError(f"{case.case_id}的refused必须是布尔值")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "answerable": case.answerable,
        "refused": refused,
        "citation_precision": supported_citations / len(citations) if citations else (1.0 if not case.answerable else 0.0),
        "citation_precision_hits": supported_citations,
        "citation_precision_total": len(citations),
        "citation_recall": len(cited_evidence) / len(relevant_evidence) if relevant_evidence else 1.0,
        "citation_recall_hits": len(cited_evidence),
        "citation_recall_total": len(relevant_evidence),
        "faithfulness": faithfulness,
        "faithfulness_supported": faithfulness_supported,
        "faithfulness_total": faithfulness_total,
        "judge_failed": judge_failed,
        "first_token_ms": record.get("first_token_ms"),
        "total_response_ms": record.get("total_response_ms"),
    }


def aggregate_answer_rows(rows: list[dict]) -> dict:
    no_answer = [row for row in rows if not row["answerable"]]
    answerable = [row for row in rows if row["answerable"]]
    faithful = [row for row in rows if row["faithfulness"] is not None]
    citation_precision_hits = sum(row["citation_precision_hits"] for row in rows)
    citation_precision_total = sum(row["citation_precision_total"] for row in rows)
    citation_recall_hits = sum(row["citation_recall_hits"] for row in rows)
    citation_recall_total = sum(row["citation_recall_total"] for row in rows)
    return {
        "cases": len(rows),
        "citation_precision": {
            "hits": citation_precision_hits,
            "total": citation_precision_total,
            "value": citation_precision_hits / citation_precision_total if citation_precision_total else 0.0,
        },
        "citation_recall": {
            "hits": citation_recall_hits,
            "total": citation_recall_total,
            "value": citation_recall_hits / citation_recall_total if citation_recall_total else 0.0,
        },
        "faithfulness": {
            "supported": sum(row["faithfulness_supported"] for row in faithful),
            "total": sum(row["faithfulness_total"] for row in faithful),
            "value": (
                sum(row["faithfulness_supported"] for row in faithful)
                / sum(row["faithfulness_total"] for row in faithful)
                if sum(row["faithfulness_total"] for row in faithful)
                else 0.0
            ),
            "judge_failures": sum(row["judge_failed"] for row in rows),
        },
        "no_answer_accuracy": {
            "correct": sum(row["refused"] for row in no_answer),
            "total": len(no_answer),
            "value": sum(row["refused"] for row in no_answer) / len(no_answer) if no_answer else 0.0,
        },
        "answerable_response_rate": {
            "correct": sum(not row["refused"] for row in answerable),
            "total": len(answerable),
            "value": sum(not row["refused"] for row in answerable) / len(answerable) if answerable else 0.0,
        },
        "first_token_p50_ms": percentile(
            [row["first_token_ms"] for row in rows if isinstance(row["first_token_ms"], int)], 0.5
        ),
        "first_token_p95_ms": percentile(
            [row["first_token_ms"] for row in rows if isinstance(row["first_token_ms"], int)], 0.95
        ),
        "total_response_p50_ms": percentile(
            [row["total_response_ms"] for row in rows if isinstance(row["total_response_ms"], int)], 0.5
        ),
        "total_response_p95_ms": percentile(
            [row["total_response_ms"] for row in rows if isinstance(row["total_response_ms"], int)], 0.95
        ),
    }


def evaluate_answer_records(cases, records: list[dict]) -> dict:
    records_by_id = {str(record.get("case_id", "")): record for record in records}
    if len(records_by_id) != len(records):
        raise EvaluationDataError("答案记录case_id为空或重复")
    missing = [case.case_id for case in cases if case.case_id not in records_by_id]
    extras = sorted(set(records_by_id) - {case.case_id for case in cases})
    if missing or extras:
        raise EvaluationDataError(
            f"答案记录与数据集不一致，缺失{len(missing)}条，多余{len(extras)}条"
        )
    rows = [evaluate_answer_record(case, records_by_id[case.case_id]) for case in cases]
    categories = sorted({row["category"] for row in rows})
    return {
        "summary": aggregate_answer_rows(rows),
        "category_summary": {
            category: aggregate_answer_rows([row for row in rows if row["category"] == category])
            for category in categories
        },
        "rows": rows,
    }
