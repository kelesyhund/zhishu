import hashlib
import json

import numpy as np
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from api.models import Document, EmbeddingSpace, KnowledgeBase, Paragraph, ParagraphEmbedding
from api.services.vector_storage import resolve_embedding_space
from api.services.workspaces import ensure_personal_workspace


ALLOWED_SIZES = {10_000, 50_000, 100_000}
ALLOWED_DIMENSIONS = {256, 384, 768, 1024, 1536}


class Command(BaseCommand):
    help = "构造隔离且可重复的 pgvector 压测数据（只允许 10k/50k/100k）"

    def add_arguments(self, parser):
        parser.add_argument("target_size", type=int)
        parser.add_argument("--dimension", type=int, default=256)
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument(
            "--legacy-only",
            action="store_true",
            help="只写Paragraph JSON向量，用于验证真实回填流程",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("压测数据只能写入隔离的PostgreSQL环境")
        target_size = int(options["target_size"])
        dimension = int(options["dimension"])
        batch_size = max(100, min(int(options["batch_size"]), 2_000))
        if target_size not in ALLOWED_SIZES:
            raise CommandError("target_size只允许10000、50000或100000")
        if dimension not in ALLOWED_DIMENSIONS:
            raise CommandError("dimension 不在 pgvector 压测白名单")

        username = f"pgbench-owner-{dimension}"
        knowledge_name = f"pgbench-vector-{dimension}"
        user, created = User.objects.get_or_create(username=username)
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        workspace = ensure_personal_workspace(user)
        knowledge_base, _ = KnowledgeBase.objects.get_or_create(
            owner=user,
            workspace=workspace,
            name=knowledge_name,
            defaults={"description": "Isolated synthetic pgvector benchmark data"},
        )
        document, _ = Document.objects.get_or_create(
            knowledge_base=knowledge_base,
            name=f"pgbench-vector-{dimension}.txt",
            defaults={
                "file": f"pgbench/pgvector-{dimension}.txt",
                "status": Document.Status.SUCCESS,
                "embedding_signature": "local-hash-256",
            },
        )
        if document.status != Document.Status.SUCCESS:
            document.status = Document.Status.SUCCESS
            document.save(update_fields=["status"])
        space = resolve_embedding_space(knowledge_base, dimension, create=True, ready=True)
        existing = Paragraph.objects.filter(document=document).count()
        if existing > target_size:
            raise CommandError("现有临时数据大于目标；命令不会隐式删除数据")

        for start in range(existing + 1, target_size + 1, batch_size):
            stop = min(target_size + 1, start + batch_size)
            positions = list(range(start, stop))
            # Each batch is deterministic for its absolute start position.  Normalized
            # float32 rows keep cosine-distance ground truth stable and memory bounded.
            generator = np.random.default_rng(20260915 + start)
            matrix = generator.standard_normal((len(positions), dimension), dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.maximum(norms, np.finfo(np.float32).eps)
            vectors = matrix.tolist()
            paragraphs = []
            for position, vector in zip(positions, vectors):
                content = f"pgvector synthetic paragraph {position} dimension {dimension}"
                paragraphs.append(
                    Paragraph(
                        document=document,
                        position=position,
                        content=content,
                        embedding=vector,
                        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    )
                )
            with transaction.atomic():
                Paragraph.objects.bulk_create(paragraphs, batch_size=batch_size)
                if not options["legacy_only"]:
                    ParagraphEmbedding.objects.bulk_create(
                        [
                            ParagraphEmbedding(
                                paragraph=paragraph,
                                knowledge_base=knowledge_base,
                                space=space,
                                embedding=vector,
                                dimension=dimension,
                                content_hash=paragraph.content_sha256,
                            )
                            for paragraph, vector in zip(paragraphs, vectors)
                        ],
                        batch_size=batch_size,
                    )
            self.stdout.write(f"seeded={stop - 1}/{target_size}")

        document.paragraph_count = target_size
        document.save(update_fields=["paragraph_count"])
        payload = {
            "knowledge_base_id": knowledge_base.id,
            "document_id": document.id,
            "space_id": space.id,
            "target_size": target_size,
            "legacy_count": Paragraph.objects.filter(document=document).count(),
            "pgvector_count": ParagraphEmbedding.objects.filter(space=space).count(),
            "legacy_only": bool(options["legacy_only"]),
        }
        self.stdout.write(self.style.SUCCESS(json.dumps(payload, ensure_ascii=False)))
