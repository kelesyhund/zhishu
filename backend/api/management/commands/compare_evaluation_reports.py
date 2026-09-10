import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "比较两个同数据集检索报告，拒绝跨数据集伪比较"

    def add_arguments(self, parser):
        parser.add_argument("--baseline", required=True)
        parser.add_argument("--experimental", required=True)
        parser.add_argument("--baseline-strategy", default="RRF")
        parser.add_argument("--experimental-strategy", default="RRF_RERANK")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        try:
            baseline = json.loads(Path(options["baseline"]).read_text(encoding="utf-8"))
            experimental = json.loads(Path(options["experimental"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError("报告不存在或不是合法JSON") from exc
        if baseline.get("dataset_sha256") != experimental.get("dataset_sha256"):
            raise CommandError("两份报告的数据集SHA256不同，不能直接比较")
        base = baseline.get("summary", {}).get(options["baseline_strategy"])
        exp = experimental.get("summary", {}).get(options["experimental_strategy"])
        if not base or not exp:
            raise CommandError("指定策略在报告中不存在")
        base_recall = base["recall_at_5"].get("value") if isinstance(base["recall_at_5"], dict) else base["recall_at_5"]
        exp_recall = exp["recall_at_5"].get("value") if isinstance(exp["recall_at_5"], dict) else exp["recall_at_5"]
        comparison = {
            "dataset_sha256": baseline["dataset_sha256"],
            "baseline_strategy": options["baseline_strategy"],
            "experimental_strategy": options["experimental_strategy"],
            "recall_at_5": {"baseline": base_recall, "experimental": exp_recall, "delta": exp_recall - base_recall},
            "ndcg_at_10": {"baseline": base["ndcg_at_10"], "experimental": exp["ndcg_at_10"], "delta": exp["ndcg_at_10"] - base["ndcg_at_10"]},
            "p95_ms": {"baseline": base["latency_p95_ms"], "experimental": exp["latency_p95_ms"], "delta": exp["latency_p95_ms"] - base["latency_p95_ms"]},
            "quality_gate_passed": exp_recall > base_recall and exp["ndcg_at_10"] > base["ndcg_at_10"],
        }
        rendered = json.dumps(comparison, ensure_ascii=False, indent=2)
        if options.get("output"):
            output = Path(options["output"]).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        self.stdout.write(rendered)
