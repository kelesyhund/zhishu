import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import load_stage11_cases, read_jsonl


class Command(BaseCommand):
    help = "把AUTO_DRAFT候选集导出成一次性人工审核CSV包"

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--manifest")

    def handle(self, *args, **options):
        dataset = Path(options["dataset"]).resolve()
        output = Path(options["output"]).resolve()
        manifest = Path(options["manifest"]).resolve() if options.get("manifest") else None
        try:
            load_stage11_cases(dataset, manifest_path=manifest)
            rows = read_jsonl(dataset)
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "case_id", "query", "category", "split", "answerable", "gold_evidence",
            "answer_key_points", "notes", "decision", "review_comment",
        ]
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    **{field: row.get(field, "") for field in fields},
                    "gold_evidence": json.dumps(row.get("gold_evidence", []), ensure_ascii=False),
                    "answer_key_points": json.dumps(row.get("answer_key_points", []), ensure_ascii=False),
                    "decision": "",
                    "review_comment": "",
                })
        self.stdout.write(self.style.SUCCESS(f"审核包已导出：{output}（{len(rows)}题，尚未视为人工确认）"))
