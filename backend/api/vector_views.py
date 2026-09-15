import math

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count
from rest_framework import serializers, status
from rest_framework.response import Response

from .models import EmbeddingSpace, KnowledgeBase, Paragraph, ParagraphEmbedding, VectorMigrationRun
from .services.audit import record_audit_event
from .services.vector_storage import (
    cancel_migration_run,
    create_migration_run,
    dispatch_migration_run,
    resolve_embedding_space,
    retry_migration_run,
    workspace_vector_status,
    embedding_space_signature,
)
from .workspace_api import WorkspaceAPIView


def ok(data=None, message="success", response_status=200):
    return Response({"code": 200, "message": message, "data": data}, status=response_status)


class EmbeddingSpaceSerializer(serializers.ModelSerializer):
    vector_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = EmbeddingSpace
        fields = [
            "id", "signature", "model_name", "revision", "dimension", "distance_metric",
            "status", "indexed", "vector_count", "created_at", "updated_at",
        ]


class VectorMigrationSerializer(serializers.ModelSerializer):
    space = EmbeddingSpaceSerializer(read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = VectorMigrationRun
        fields = [
            "id", "space", "status", "total_count", "succeeded_count", "failed_count",
            "skipped_count", "cursor_id", "batch_size", "progress", "error_message",
            "created_at", "started_at", "finished_at", "updated_at",
        ]

    def get_progress(self, obj):
        processed = obj.succeeded_count + obj.failed_count + obj.skipped_count
        return min(100, round(processed * 100 / obj.total_count)) if obj.total_count else 100


class VectorIndexStatusView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "vector.manage")
        payload = workspace_vector_status(self.workspace(request))
        payload["recent_migrations"] = VectorMigrationSerializer(
            VectorMigrationRun.objects.filter(workspace=self.workspace(request))
            .select_related("space")[:5], many=True
        ).data
        return ok(payload)


class VectorMigrationListView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "vector.manage")
        page_size = max(1, min(int(request.query_params.get("page_size", 20)), 100))
        paginator = Paginator(
            VectorMigrationRun.objects.filter(workspace=self.workspace(request)).select_related("space"),
            page_size,
        )
        page = paginator.get_page(request.query_params.get("page", 1))
        return ok({
            "items": VectorMigrationSerializer(page.object_list, many=True).data,
            "total": paginator.count,
            "page": page.number,
            "page_size": page_size,
            "total_pages": paginator.num_pages,
        })

    def post(self, request):
        access = self.require(request, "vector.manage")
        if connection.vendor != "postgresql":
            return Response(
                {"code": 409, "message": "向量迁移仅能在PostgreSQL + pgvector环境执行", "data": {}},
                status=409,
            )
        try:
            knowledge_base_id = int(request.data.get("knowledge_base_id"))
            batch_size = int(request.data.get("batch_size", 200))
        except (TypeError, ValueError):
            return Response({"code": 400, "message": "请选择知识库并填写有效批大小", "data": {}}, status=400)
        knowledge_base = KnowledgeBase.objects.filter(pk=knowledge_base_id, workspace=access.workspace).first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": {}}, status=404)
        paragraph = Paragraph.objects.filter(
            document__knowledge_base=knowledge_base, document__status="SUCCESS"
        ).exclude(embedding=[]).values_list("embedding", flat=True).first()
        if not isinstance(paragraph, list) or not paragraph:
            return Response({"code": 409, "message": "知识库没有可迁移的Legacy向量", "data": {}}, status=409)
        space = resolve_embedding_space(knowledge_base, len(paragraph), create=True)
        run = create_migration_run(access.workspace, space, request.user, batch_size)
        if not run.celery_task_id:
            run = dispatch_migration_run(run)
        record_audit_event(
            request, organization=access.workspace.organization, workspace=access.workspace,
            action="vector.migration.create", resource_type="VectorMigrationRun", resource_id=run.id,
        )
        return ok(VectorMigrationSerializer(run).data, "向量迁移已创建", status.HTTP_202_ACCEPTED)


class VectorMigrationDetailView(WorkspaceAPIView):
    def get_object(self, request, migration_id):
        self.require(request, "vector.manage")
        return VectorMigrationRun.objects.filter(
            pk=migration_id, workspace=self.workspace(request)
        ).select_related("space").first()

    def get(self, request, migration_id):
        run = self.get_object(request, migration_id)
        if not run:
            return Response({"code": 404, "message": "迁移任务不存在", "data": {}}, status=404)
        return ok(VectorMigrationSerializer(run).data)


class VectorMigrationActionView(VectorMigrationDetailView):
    def post(self, request, migration_id, action):
        run = self.get_object(request, migration_id)
        if not run:
            return Response({"code": 404, "message": "迁移任务不存在", "data": {}}, status=404)
        if action == "retry":
            run = retry_migration_run(run)
            message = "迁移任务已重试"
        elif action == "cancel":
            run = cancel_migration_run(run)
            message = "取消请求已提交"
        else:
            return Response({"code": 404, "message": "操作不存在", "data": {}}, status=404)
        return ok(VectorMigrationSerializer(run).data, message)


class KnowledgeBaseVectorStatusView(WorkspaceAPIView):
    def get_knowledge_base(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        return KnowledgeBase.objects.filter(pk=knowledge_id, workspace=self.workspace(request)).first()

    def get(self, request, knowledge_id):
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": {}}, status=404)
        legacy = Paragraph.objects.filter(document__knowledge_base=knowledge_base).exclude(embedding=[]).count()
        signature, _, _, _ = embedding_space_signature(knowledge_base)
        current_vectors = ParagraphEmbedding.objects.filter(
            paragraph__document__knowledge_base=knowledge_base,
            space__signature=signature,
        )
        vector = current_vectors.values("paragraph_id").distinct().count()
        dimensions = list(
            current_vectors
            .values("dimension").annotate(count=Count("id")).order_by("dimension")
        )
        return ok({
            "knowledge_base_id": knowledge_base.id,
            "legacy_vector_count": legacy,
            "pgvector_row_count": vector,
            "coverage_ratio": round(vector / legacy, 6) if legacy else 1.0,
            "dimensions": dimensions,
        })

    def post(self, request, knowledge_id):
        self.require(request, "vector.manage")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": {}}, status=404)
        rows = ParagraphEmbedding.objects.filter(
            paragraph__document__knowledge_base=knowledge_base
        ).select_related("paragraph")[:100]
        checked = mismatched = 0
        max_delta = 0.0
        for row in rows:
            legacy = row.paragraph.embedding
            vector = list(row.embedding)
            if not isinstance(legacy, list) or len(legacy) != len(vector):
                mismatched += 1
                continue
            delta = max((abs(float(a) - float(b)) for a, b in zip(legacy, vector)), default=0.0)
            max_delta = max(max_delta, delta)
            checked += 1
            if not math.isfinite(delta) or delta > 1e-5:
                mismatched += 1
        return ok({"checked_count": checked, "mismatched_count": mismatched, "max_absolute_delta": round(max_delta, 8)})
