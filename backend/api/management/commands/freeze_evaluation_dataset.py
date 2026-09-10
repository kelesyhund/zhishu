import json
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import (
    assert_no_cross_dataset_duplicates,
    file_sha256,
    load_stage11_cases,
    read_jsonl,
)


class Command(BaseCommand):
    help = "冻结已人工确认的Stage11 Test数据集并写入SHA256伴随文件"

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--dev-dataset")
        parser.add_argument("--minimum-cases", type=int, default=100)

    def handle(self, *args, **options):
        source = Path(options["dataset"]).resolve()
        output = Path(options["output"]).resolve()
        manifest = Path(options["manifest"]).resolve()
        if output.exists():
            raise CommandError("冻结输出已存在；不得静默覆盖旧测试集，请使用新版本文件名")
        try:
            cases = load_stage11_cases(
                source, manifest_path=manifest, require_approved=True, expected_split="test"
            )
            if len(cases) < options["minimum_cases"]:
                raise EvaluationDataError(
                    f"冻结测试集至少需要{options['minimum_cases']}题，当前只有{len(cases)}题"
                )
            if options.get("dev_dataset"):
                dev = load_stage11_cases(
                    Path(options["dev_dataset"]).resolve(),
                    manifest_path=manifest,
                    require_approved=True,
                    expected_split="dev",
                )
                assert_no_cross_dataset_duplicates(dev, cases)
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        rows = read_jsonl(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        canonical = "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        ) + "\n"
        output.write_text(canonical, encoding="utf-8")
        digest = file_sha256(output)
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{digest}  {output.name}\n", encoding="ascii"
        )
        metadata = {
            "dataset": output.name,
            "sha256": digest,
            "manifest_sha256": file_sha256(manifest),
            "case_count": len(cases),
            "human_approved_count": len(cases),
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "blind_test": True,
            "rule": "正式运行一次后不得根据逐题Test结果继续调参",
        }
        output.with_suffix(output.suffix + ".freeze.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.stdout.write(self.style.SUCCESS(f"冻结完成：cases={len(cases)}, sha256={digest}"))
