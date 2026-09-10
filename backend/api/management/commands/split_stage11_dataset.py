import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import load_stage11_cases, read_jsonl


class Command(BaseCommand):
    help = "将人工审核后的混合集按预先标记的split拆成Dev与待冻结Test"

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--dev-output", required=True)
        parser.add_argument("--test-output", required=True)

    def handle(self, *args, **options):
        dataset = Path(options["dataset"]).resolve()
        manifest = Path(options["manifest"]).resolve()
        dev_output = Path(options["dev_output"]).resolve()
        test_output = Path(options["test_output"]).resolve()
        if dev_output.exists() or test_output.exists():
            raise CommandError("拆分输出已存在；拒绝静默覆盖，请使用新版本文件名")
        try:
            cases = load_stage11_cases(
                dataset, manifest_path=manifest, require_approved=True
            )
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        rows = read_jsonl(dataset)
        dev = [row for row in rows if str(row.get("split", "")).lower() == "dev"]
        test = [row for row in rows if str(row.get("split", "")).lower() == "test"]
        if len(dev) + len(test) != len(cases) or not dev or not test:
            raise CommandError("拆分结果不完整或某个集合为空")
        for output, items in ((dev_output, dev), (test_output, test)):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items) + "\n",
                encoding="utf-8",
            )
        self.stdout.write(self.style.SUCCESS(f"拆分完成：dev={len(dev)}, test={len(test)}"))
