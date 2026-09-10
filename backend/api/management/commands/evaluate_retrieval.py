import csv
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from api.models import Document, DocumentSection, KnowledgeBase, Paragraph
from api.services.model_resolution import active_embedding_signature
from api.services.retrieval_evaluation import (
    EvaluationDataError,
    dataset_sha256,
    load_evaluation_cases,
    run_evaluation,
)
from api.services.stage11_evaluation import (
    file_sha256,
    load_stage11_cases,
    run_stage11_retrieval_evaluation,
)


def git_snapshot(project_root: Path) -> dict:
    def run(*arguments):
        try:
            result = subprocess.run(
                ["git", *arguments], cwd=project_root, capture_output=True,
                check=True, text=True, timeout=5,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    commit = run("rev-parse", "HEAD")
    porcelain = run("status", "--porcelain")
    return {
        "commit": commit or None,
        "workspace": "dirty" if porcelain else "clean" if commit else "unversioned",
    }


def is_stage11_dataset(path: Path) -> bool:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                return "case_id" in json.loads(line)
            except json.JSONDecodeError:
                return False
    return False


def ratio(value):
    return value["hits"] / value["total"] if value["total"] else 0.0


class Command(BaseCommand):
    help = "运行可复现的离线检索评测（兼容Stage09和Stage11数据集）"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--knowledge-base-id", required=True, type=int)
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--manifest")
        parser.add_argument("--strategies", default="VECTOR,WEIGHTED,RRF,RRF_RERANK")
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--allow-dev", action="store_true")

    def handle(self, *args, **options):
        user = User.objects.filter(username=options["username"]).first()
        if not user:
            raise CommandError("评测用户不存在")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=options["knowledge_base_id"], owner=user
        ).select_related("embedding_model_config").first()
        if not knowledge_base:
            raise CommandError("评测知识库不存在或不属于指定用户")
        dataset = Path(options["dataset"]).resolve()
        manifest = Path(options["manifest"]).resolve() if options.get("manifest") else None
        output_dir = Path(options["output_dir"]).resolve()
        strategies = [
            item.strip().upper() for item in options["strategies"].split(",") if item.strip()
        ]
        stage11 = dataset.is_file() and is_stage11_dataset(dataset)
        try:
            if stage11:
                cases = load_stage11_cases(
                    dataset,
                    manifest_path=manifest,
                    knowledge_base=knowledge_base,
                    require_approved=not options["allow_dev"],
                )
                if options["allow_dev"] and any(case.split == "test" for case in cases):
                    raise EvaluationDataError("--allow-dev不能用于Test数据集")
                evaluation = run_stage11_retrieval_evaluation(knowledge_base, cases, strategies)
            else:
                cases = load_evaluation_cases(dataset, knowledge_base)
                evaluation = run_evaluation(knowledge_base, cases, strategies)
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        documents = Document.objects.filter(knowledge_base=knowledge_base)
        paragraph_query = Paragraph.objects.filter(document__knowledge_base=knowledge_base)
        report = {
            "schema_version": "stage11-v1" if stage11 else "stage09-v1",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": dataset.name,
            "dataset_sha256": dataset_sha256(dataset),
            "manifest_sha256": file_sha256(manifest) if manifest else None,
            "case_count": len(cases),
            "reviewed_case_count": sum(
                getattr(case, "review_status", "") == "HUMAN_APPROVED" for case in cases
            ),
            "synthetic": False if stage11 else all(case.synthetic for case in cases),
            "frozen_test": bool(
                stage11 and len(cases) >= 100
                and all(case.split == "test" for case in cases)
                and all(case.review_status == "HUMAN_APPROVED" for case in cases)
            ),
            "knowledge_base_id": knowledge_base.id,
            "corpus": {
                "document_count": documents.count(),
                "unique_block_count": len({
                    block_id for values in paragraph_query.values_list("source_block_ids", flat=True)
                    for block_id in (values or [])
                }),
                "parent_count": DocumentSection.objects.filter(
                    document__knowledge_base=knowledge_base
                ).count(),
                "child_count": paragraph_query.filter(chunk_type=Paragraph.ChunkType.CHILD).count(),
                "legacy_count": paragraph_query.filter(chunk_type=Paragraph.ChunkType.LEGACY).count(),
            },
            "source": git_snapshot(Path(__file__).resolve().parents[4]),
            "hardware": {
                "platform": platform.platform(),
                "processor": platform.processor() or "unknown",
                "python": platform.python_version(),
            },
            "embedding": {
                "source": "database_config" if knowledge_base.embedding_model_config_id else "environment_or_local_fallback",
                "config_id": knowledge_base.embedding_model_config_id,
                "model_name": knowledge_base.embedding_model_config.model_name if knowledge_base.embedding_model_config_id else None,
                "signature": active_embedding_signature(knowledge_base) or "legacy",
            },
            "strategies": strategies,
            **evaluation,
        }
        prefix = "stage11-retrieval" if stage11 else "retrieval-evaluation"
        json_path = output_dir / f"{prefix}-{run_id}.json"
        csv_path = output_dir / f"{prefix}-{run_id}.csv"
        markdown_path = output_dir / f"{prefix}-{run_id}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            fields = (
                ["strategy", "case_id", "query", "category", "split", "answerable",
                 "review_status", "ranked_paragraph_ids", "matched_evidence_ids", "hit_at_1",
                 "hit_at_3", "recall_at_5_hits", "recall_at_5_total", "recall_at_5",
                 "mrr_at_10", "ndcg_at_10", "latency_ms", "rerank_applied",
                 "rerank_fallback_code"]
                if stage11 else
                ["strategy", "case_id", "question", "category", "difficulty", "synthetic",
                 "relevant_paragraph_ids", "ranked_paragraph_ids", "hit_at_1", "hit_at_3",
                 "recall_at_5", "mrr_at_10", "ndcg_at_10", "latency_ms",
                 "rerank_applied", "rerank_fallback_code"]
            )
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for strategy, rows in report["rows"].items():
                for row in rows:
                    writer.writerow({"strategy": strategy, **row})

        lines = [
            "# 检索离线评测报告", "", f"- 数据集：`{report['dataset']}`",
            f"- SHA256：`{report['dataset_sha256']}`",
            f"- 样本：{report['case_count']}，人工确认：{report['reviewed_case_count']}",
            f"- 冻结测试：{'是' if report['frozen_test'] else '否'}",
            f"- 文档/Parent/Child：{report['corpus']['document_count']}/{report['corpus']['parent_count']}/{report['corpus']['child_count']}",
            "", "| 策略 | Hit@1 | Hit@3 | Recall@5 | MRR@10 | NDCG@10 | P50 | P95 | 实际执行/降级 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for strategy in strategies:
            item = report["summary"][strategy]
            if stage11:
                recall = item["recall_at_5"]
                reranker = item["reranker"]
                recall_text = f"{recall['hits']}/{recall['total']} ({recall['value']:.4f})"
                execution = f"{reranker['applied']}/{reranker['fallback']}"
            else:
                recall_text = f"{item['recall_at_5']:.4f}"
                execution = f"0/{item['reranker_fallback']['count']}"
            lines.append(
                f"| {strategy} | {item['hit_at_1']['hits']}/{item['hit_at_1']['total']} ({ratio(item['hit_at_1']):.4f}) | "
                f"{item['hit_at_3']['hits']}/{item['hit_at_3']['total']} ({ratio(item['hit_at_3']):.4f}) | "
                f"{recall_text} | {item['mrr_at_10']:.4f} | {item['ndcg_at_10']:.4f} | "
                f"{item['latency_p50_ms']}ms | {item['latency_p95_ms']}ms | {execution} |"
            )
        lines.extend(["", "> 只有冻结、人工确认且真实Reranker实际执行的结果，才能作为真实业务效果证据。"])
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"评测完成：{json_path}"))
