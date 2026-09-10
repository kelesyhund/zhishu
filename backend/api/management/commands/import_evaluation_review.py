import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import load_stage11_cases


class Command(BaseCommand):
    help = "导入用户逐题确认后的审核CSV；只有APPROVE会进入输出数据集"

    def add_arguments(self, parser):
        parser.add_argument("--review", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--reviewed-by", required=True)
        parser.add_argument("--manifest")

    def handle(self, *args, **options):
        review = Path(options["review"]).resolve()
        output = Path(options["output"]).resolve()
        manifest = Path(options["manifest"]).resolve() if options.get("manifest") else None
        if not review.is_file():
            raise CommandError("审核CSV不存在")
        approved = []
        seen = set()
        with review.open("r", encoding="utf-8-sig", newline="") as handle:
            for line_number, row in enumerate(csv.DictReader(handle), start=2):
                case_id = str(row.get("case_id", "")).strip()
                decision = str(row.get("decision", "")).strip().upper()
                if not case_id or case_id in seen:
                    raise CommandError(f"审核CSV第{line_number}行case_id为空或重复")
                if decision not in {"APPROVE", "REJECT"}:
                    raise CommandError(f"{case_id}必须明确填写APPROVE或REJECT")
                seen.add(case_id)
                if decision == "REJECT":
                    continue
                try:
                    evidence = json.loads(row.get("gold_evidence") or "[]")
                    key_points = json.loads(row.get("answer_key_points") or "[]")
                except json.JSONDecodeError as exc:
                    raise CommandError(f"{case_id}的证据或关键点不是合法JSON") from exc
                approved.append({
                    "case_id": case_id,
                    "query": str(row.get("query", "")).strip(),
                    "category": str(row.get("category", "")).strip(),
                    "split": str(row.get("split", "")).strip().lower(),
                    "answerable": str(row.get("answerable", "")).strip().lower() in {"true", "1", "yes"},
                    "gold_evidence": evidence,
                    "answer_key_points": key_points,
                    "review_status": "HUMAN_APPROVED",
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                    "reviewed_by": options["reviewed_by"][:100],
                    "notes": str(row.get("review_comment") or row.get("notes") or "")[:1000],
                })
        if not approved:
            raise CommandError("审核结果中没有APPROVE题目")
        output.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in approved) + "\n"
        output.write_text(content, encoding="utf-8")
        try:
            load_stage11_cases(output, manifest_path=manifest, require_approved=True)
        except EvaluationDataError as exc:
            output.unlink(missing_ok=True)
            raise CommandError(f"审核导入校验失败，未保留输出：{exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"审核导入完成：approved={len(approved)}, output={output}"))
