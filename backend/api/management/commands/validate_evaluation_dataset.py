from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from api.models import KnowledgeBase
from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import (
    assert_no_cross_dataset_duplicates,
    file_sha256,
    load_stage11_cases,
)


class Command(BaseCommand):
    help = "严格校验Stage11评测数据格式、审核状态、证据映射和Dev/Test泄漏"

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--manifest")
        parser.add_argument("--other-dataset")
        parser.add_argument("--username")
        parser.add_argument("--knowledge-base-id", type=int)
        parser.add_argument("--require-approved", action="store_true")
        parser.add_argument("--minimum-cases", type=int, default=1)

    def handle(self, *args, **options):
        knowledge_base = None
        if options.get("knowledge_base_id") or options.get("username"):
            if not options.get("knowledge_base_id") or not options.get("username"):
                raise CommandError("证据映射校验必须同时提供--username和--knowledge-base-id")
            user = User.objects.filter(username=options["username"]).first()
            knowledge_base = KnowledgeBase.objects.filter(
                pk=options["knowledge_base_id"], owner=user
            ).first()
            if not knowledge_base:
                raise CommandError("评测知识库不存在或不属于指定用户")
        dataset = Path(options["dataset"]).resolve()
        manifest = Path(options["manifest"]).resolve() if options.get("manifest") else None
        try:
            cases = load_stage11_cases(
                dataset,
                manifest_path=manifest,
                knowledge_base=knowledge_base,
                require_approved=options["require_approved"],
            )
            if len(cases) < options["minimum_cases"]:
                raise EvaluationDataError(
                    f"数据集只有{len(cases)}题，少于要求的{options['minimum_cases']}题"
                )
            if options.get("other_dataset"):
                other = load_stage11_cases(
                    Path(options["other_dataset"]).resolve(), manifest_path=manifest
                )
                assert_no_cross_dataset_duplicates(cases, other)
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        categories = {}
        for case in cases:
            categories[case.category] = categories.get(case.category, 0) + 1
        self.stdout.write(self.style.SUCCESS(
            f"校验通过：cases={len(cases)}, approved={sum(case.review_status == 'HUMAN_APPROVED' for case in cases)}, "
            f"sha256={file_sha256(dataset)}, categories={categories}"
        ))
