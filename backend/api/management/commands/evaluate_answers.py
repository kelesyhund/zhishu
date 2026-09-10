import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import (
    evaluate_answer_records,
    file_sha256,
    load_stage11_cases,
    read_jsonl,
)


class Command(BaseCommand):
    help = "基于保存的答案、引用和显式Claim判定计算答案级指标；不会调用付费LLM"

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--answers", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--judge-model", default="none-manual-or-precomputed")
        parser.add_argument("--judge-prompt-version", default="stage11-faithfulness-v1")
        parser.add_argument("--judge-config-revision", default="none")

    def handle(self, *args, **options):
        dataset = Path(options["dataset"]).resolve()
        answers = Path(options["answers"]).resolve()
        manifest = Path(options["manifest"]).resolve()
        output_dir = Path(options["output_dir"]).resolve()
        try:
            cases = load_stage11_cases(
                dataset, manifest_path=manifest, require_approved=True
            )
            evaluation = evaluate_answer_records(cases, read_jsonl(answers))
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report = {
            "schema_version": "stage11-answer-v1",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": dataset.name,
            "dataset_sha256": file_sha256(dataset),
            "answers_sha256": file_sha256(answers),
            "manifest_sha256": file_sha256(manifest),
            "case_count": len(cases),
            "judge": {
                "type": "precomputed_structured_judgment",
                "model": options["judge_model"],
                "prompt_version": options["judge_prompt_version"],
                "temperature": 0,
                "config_revision": options["judge_config_revision"],
                "hidden_reasoning_stored": False,
            },
            "hardware": {
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            **evaluation,
        }
        path = output_dir / f"stage11-answers-{run_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = report["summary"]
        markdown = [
            "# Stage11答案级评测", "", f"- 数据集：`{dataset.name}`",
            f"- 数据集SHA256：`{report['dataset_sha256']}`", f"- 样本：{len(cases)}",
            f"- Judge失败：{summary['faithfulness']['judge_failures']}", "",
            "| 指标 | 分子/分母 | 值 |", "|---|---:|---:|",
            f"| Citation Precision | {summary['citation_precision']['hits']}/{summary['citation_precision']['total']} | {summary['citation_precision']['value']:.4f} |",
            f"| Citation Recall | {summary['citation_recall']['hits']}/{summary['citation_recall']['total']} | {summary['citation_recall']['value']:.4f} |",
            f"| Faithfulness | {summary['faithfulness']['supported']}/{summary['faithfulness']['total']} | {summary['faithfulness']['value']:.4f} |",
            f"| No-answer Accuracy | {summary['no_answer_accuracy']['correct']}/{summary['no_answer_accuracy']['total']} | {summary['no_answer_accuracy']['value']:.4f} |",
            f"| 有答案正常回答率 | {summary['answerable_response_rate']['correct']}/{summary['answerable_response_rate']['total']} | {summary['answerable_response_rate']['value']:.4f} |",
        ]
        path.with_suffix(".md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"答案评测完成：{path}"))
