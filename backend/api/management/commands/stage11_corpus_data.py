import json
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from api.models import Document, KnowledgeBase
from api.services.document_processor import process_document
from api.services.stage11_evaluation import EvaluationDataError, load_corpus_manifest


USERNAME = "stage11-eval-user"
KNOWLEDGE_NAME = "stage11-docker-technical-assistant"


class Command(BaseCommand):
    help = "创建或清理独立Stage11 Docker真实语料评测知识库"

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--setup", action="store_true")
        action.add_argument("--cleanup", action="store_true")
        parser.add_argument("--manifest", default="../evals/corpus/manifest.jsonl")

    def handle(self, *args, **options):
        if options["cleanup"]:
            knowledge = KnowledgeBase.objects.filter(
                owner__username=USERNAME, name=KNOWLEDGE_NAME
            )
            deleted, _ = knowledge.delete()
            user_deleted, _ = User.objects.filter(
                username=USERNAME, knowledge_bases__isnull=True
            ).delete()
            self.stdout.write(self.style.SUCCESS(
                f"Stage11临时评测数据已清理：knowledge_rows={deleted}, user_rows={user_deleted}"
            ))
            return

        if KnowledgeBase.objects.filter(owner__username=USERNAME, name=KNOWLEDGE_NAME).exists():
            raise CommandError("Stage11评测知识库已存在；拒绝覆盖，请先显式执行--cleanup")
        manifest_path = Path(options["manifest"]).resolve()
        try:
            manifest = load_corpus_manifest(manifest_path)
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        included = [entry for entry in manifest.values() if entry.get("included", True)]
        if not 30 <= len(included) <= 50:
            raise CommandError(f"真实语料必须为30到50份，当前为{len(included)}份")
        user, _ = User.objects.get_or_create(username=USERNAME, defaults={"is_active": False})
        knowledge = KnowledgeBase.objects.create(
            owner=user,
            name=KNOWLEDGE_NAME,
            description="Stage11 Docker Engine与Compose公开技术文档评测语料",
            retrieval_mode=KnowledgeBase.RetrievalMode.HYBRID,
            fusion_method=KnowledgeBase.FusionMethod.RRF,
            retrieval_top_k=5,
            vector_candidate_k=30,
            keyword_candidate_k=30,
            rrf_k=60,
            rerank_candidate_k=20,
            max_context_chars=8000,
        )
        created = []
        try:
            for index, entry in enumerate(included, start=1):
                source_path = (manifest_path.parent / entry["local_path"]).resolve()
                if not source_path.is_file():
                    raise CommandError(f"语料文件不存在：{entry['source_id']}")
                document = Document(
                    knowledge_base=knowledge,
                    name=f"{entry['source_id']}.md",
                    source_id=entry["source_id"],
                    source_sha256=entry["sha256"],
                    parser_type=Document.ParserType.MARKDOWN,
                    chunk_strategy=Document.ChunkStrategy.PARENT_CHILD,
                    parent_max_tokens=1500,
                    child_target_tokens=400,
                    child_overlap_tokens=60,
                    status=Document.Status.PROCESSING,
                )
                document.file.save(
                    f"{entry['source_id']}.md",
                    ContentFile(source_path.read_bytes()),
                    save=False,
                )
                document.save()
                process_document(document)
                created.append(document.id)
                self.stdout.write(f"[{index}/{len(included)}] {entry['source_id']}")
        except Exception:
            # 删除范围仅限刚创建且名称固定的Stage11知识库，Signal同时清理其上传副本。
            knowledge.delete()
            raise
        knowledge.refresh_from_db()
        summary = {
            "username": USERNAME,
            "knowledge_base_id": knowledge.id,
            "documents": len(created),
            "parents": sum(document.sections.count() for document in knowledge.documents.all()),
            "children": sum(document.paragraphs.count() for document in knowledge.documents.all()),
        }
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))
