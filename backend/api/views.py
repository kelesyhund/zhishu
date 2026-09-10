from pathlib import Path
from dataclasses import replace

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models import Count, Max, Prefetch, Q
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AgentRun,
    Conversation,
    Document,
    DocumentProcessingTask,
    KnowledgeBase,
    ModelConfig,
    Paragraph,
    ToolExecution,
)
from .serializers import (
    AgentConfigSerializer,
    AgentRunSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    ConversationTitleSerializer,
    DocumentChunkingConfigSerializer,
    DocumentSerializer,
    DocumentProcessingTaskDetailSerializer,
    DocumentProcessingTaskListSerializer,
    KnowledgeBaseSerializer,
    KnowledgeBaseModelSelectionSerializer,
    MessageSerializer,
    ModelConfigReadSerializer,
    ModelConfigWriteSerializer,
    ParagraphSerializer,
    RetrievalConfigSerializer,
    RetrievalCompareRequestSerializer,
    RetrievalDebugRequestSerializer,
    RegisterSerializer,
)
from .services.agent_executor import stream_agent_run
from .services.audit import record_audit_event
from .services.agent_persistence import create_agent_run
from .services.agent_tools.registry import available_tools_metadata
from .services.conversations import (
    public_references,
    record_user_question_with_message,
    save_assistant_message,
)
from .services.document_parser import SUPPORTED_EXTENSIONS
from .services.document_chunking import preview_document_chunks
from .services.document_tasks import (
    create_processing_task,
    dispatch_processing_task,
    request_task_cancel,
    retry_processing_task,
)
from .services.model_clients import ModelServiceError
from .services.model_connectivity import test_model_config
from .services.model_resolution import model_status
from .services.rag import search_paragraphs, sse, stream_answer
from .services.reranker import reranker_capabilities
from .services.retrieval import (
    prepare_retrieval,
    resolve_retrieval_settings,
    retrieve_candidates,
    retrieve_from_prepared,
)
from .services.workspace_permissions import resolve_workspace_access
from .workspace_api import WorkspaceAPIView


def ok(data=None, message="success"):
    return Response({"code": 200, "message": message, "data": data})


def validation_error_response(serializer):
    """把 DRF 字段错误收敛为项目统一响应，同时保留字段级详情。"""
    def first_error(value):
        if isinstance(value, dict):
            for nested in value.values():
                result = first_error(nested)
                if result:
                    return result
        elif isinstance(value, (list, tuple)):
            for nested in value:
                result = first_error(nested)
                if result:
                    return result
        elif value:
            return str(value)
        return ""

    first_message = first_error(serializer.errors) or "请求参数校验失败"
    return Response(
        {"code": 400, "message": first_message, "data": serializer.errors},
        status=400,
    )


def get_owned_document(request, knowledge_id, document_id):
    workspace = resolve_workspace_access(request).workspace
    return Document.objects.filter(
        pk=document_id,
        knowledge_base_id=knowledge_id,
        knowledge_base__workspace=workspace,
    ).select_related("knowledge_base__embedding_model_config").first()


def owned_processing_tasks(request, knowledge_id):
    workspace = resolve_workspace_access(request).workspace
    return DocumentProcessingTask.objects.filter(
        document__knowledge_base_id=knowledge_id,
        document__knowledge_base__workspace=workspace,
    ).select_related("document")


def asynchronous_document_payload(document, processing_task):
    return {
        "document": DocumentSerializer(document).data,
        "task": DocumentProcessingTaskDetailSerializer(processing_task).data,
    }


def owned_conversation_queryset(request, knowledge_id):
    workspace = resolve_workspace_access(request).workspace
    return Conversation.objects.filter(
        knowledge_base_id=knowledge_id,
        knowledge_base__workspace=workspace,
        owner=request.user,
    ).annotate(
        message_count=Count("messages"),
        last_message_at=Max("messages__created_at"),
    )


def get_owned_conversation(request, knowledge_id, conversation_id):
    try:
        normalized_id = int(conversation_id)
    except (TypeError, ValueError):
        return None
    if normalized_id <= 0:
        return None
    return owned_conversation_queryset(request, knowledge_id).filter(pk=normalized_id).first()


def agent_config_payload(knowledge_base):
    chat = model_status(knowledge_base)["chat"]
    return {
        **AgentConfigSerializer(knowledge_base).data,
        "available_tools": available_tools_metadata(),
        "chat_model_status": {
            "available": chat["source"] != "LOCAL",
            "source": chat["source"],
            "label": chat["label"],
        },
    }


class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return ok({"status": "ok"})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user = authenticate(username=request.data.get("username"), password=request.data.get("password"))
        if not user:
            return Response({"code": 401, "message": "用户名或密码错误", "data": None}, status=401)
        token, _ = Token.objects.get_or_create(user=user)
        return ok({"token": token.key, "username": user.username})


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not settings.ALLOW_USER_REGISTRATION:
            return Response(
                {"code": 403, "message": "系统当前未开放用户注册，请联系管理员", "data": None},
                status=403,
            )
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer)
        try:
            with transaction.atomic():
                user = serializer.save()
                token = Token.objects.create(user=user)
        except IntegrityError:
            return Response(
                {"code": 400, "message": "该用户名已被使用", "data": {"username": ["该用户名已被使用"]}},
                status=400,
            )
        return ok({"token": token.key, "username": user.username}, "注册成功")


class KnowledgeBaseListView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "knowledge.read")
        queryset = KnowledgeBase.objects.filter(workspace=self.workspace(request))
        keyword = request.query_params.get("keyword", "").strip()
        if keyword:
            queryset = queryset.filter(Q(name__icontains=keyword) | Q(description__icontains=keyword))

        try:
            page_size = int(request.query_params.get("page_size", 12))
        except (TypeError, ValueError):
            page_size = 12
        page_size = max(1, min(page_size, 100))

        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(request.query_params.get("page", 1))
        return ok(
            {
                "items": KnowledgeBaseSerializer(page.object_list, many=True).data,
                "total": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages if paginator.count else 0,
            }
        )

    def post(self, request):
        access = self.require(request, "knowledge.write")
        serializer = KnowledgeBaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        knowledge_base = serializer.save(owner=request.user, workspace=access.workspace)
        record_audit_event(
            request, organization=access.workspace.organization, workspace=access.workspace,
            action="knowledge.create", resource_type="KnowledgeBase", resource_id=knowledge_base.pk,
        )
        return ok(KnowledgeBaseSerializer(knowledge_base).data, "知识库创建成功")


class KnowledgeBaseDetailView(WorkspaceAPIView):
    def get_knowledge_base(self, request, pk):
        return KnowledgeBase.objects.filter(pk=pk, workspace=self.workspace(request)).select_related(
            "chat_model_config",
            "embedding_model_config",
        ).first()

    def get(self, request, pk):
        self.require(request, "knowledge.read")
        knowledge_base = self.get_knowledge_base(request, pk)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        return ok(KnowledgeBaseSerializer(knowledge_base).data)

    def patch(self, request, pk):
        self.require(request, "knowledge.write")
        knowledge_base = self.get_knowledge_base(request, pk)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        serializer = KnowledgeBaseSerializer(knowledge_base, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        knowledge_base = serializer.save()
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="knowledge.update", resource_type="KnowledgeBase", resource_id=knowledge_base.pk,
            metadata={"changed_fields": list(request.data.keys())},
        )
        return ok(KnowledgeBaseSerializer(knowledge_base).data, "知识库修改成功")

    def delete(self, request, pk):
        self.require(request, "knowledge.write")
        knowledge_base = self.get_knowledge_base(request, pk)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        workspace = self.workspace(request)
        resource_id = knowledge_base.pk
        try:
            knowledge_base.delete()
        except ProtectedError:
            return Response(
                {
                    "code": 409,
                    "message": "知识库正被AI应用使用，请先解除应用关联",
                    "data": None,
                },
                status=409,
            )
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="knowledge.delete", resource_type="KnowledgeBase", resource_id=resource_id,
        )
        return ok(True, "知识库已删除")


class DocumentListView(WorkspaceAPIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_knowledge_base(self, request, pk):
        return KnowledgeBase.objects.filter(pk=pk, workspace=self.workspace(request)).first()

    def get(self, request, pk):
        self.require(request, "knowledge.read")
        knowledge_base = self.get_knowledge_base(request, pk)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        documents = knowledge_base.documents.select_related("knowledge_base__embedding_model_config")
        return ok(DocumentSerializer(documents, many=True).data)

    def post(self, request, pk):
        self.require(request, "document.process")
        knowledge_base = self.get_knowledge_base(request, pk)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        upload = request.FILES.get("file")
        if not upload:
            return Response({"code": 400, "message": "请选择文件", "data": None}, status=400)
        if upload.size > settings.MAX_UPLOAD_SIZE:
            return Response({"code": 400, "message": "文件不能超过10MB", "data": None}, status=400)
        if Path(upload.name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            return Response(
                {"code": 400, "message": "仅支持TXT、Markdown、PDF和DOCX", "data": None},
                status=400,
            )

        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if len(idempotency_key) > 100:
            return Response({"code": 400, "message": "幂等键不能超过100个字符", "data": None}, status=400)
        if idempotency_key:
            existing = (
                owned_processing_tasks(request, pk)
                .filter(idempotency_key=idempotency_key)
                .first()
            )
            if existing:
                response_status = 503 if existing.status == DocumentProcessingTask.Status.ENQUEUE_FAILED else 202
                return Response(
                    {
                        "code": response_status,
                        "message": "任务队列暂时不可用，请稍后重试"
                        if response_status == 503
                        else "文档已进入处理队列",
                        "data": asynchronous_document_payload(existing.document, existing),
                    },
                    status=response_status,
                )

        with transaction.atomic():
            document = Document.objects.create(
                knowledge_base=knowledge_base,
                name=upload.name,
                file=upload,
                chunk_strategy=Document.ChunkStrategy.PARENT_CHILD,
            )
            processing_task, _ = create_processing_task(
                document,
                DocumentProcessingTask.TaskType.UPLOAD,
                idempotency_key=idempotency_key,
            )
            dispatch = dispatch_processing_task(processing_task.id)

        document.refresh_from_db()
        processing_task = dispatch.processing_task
        processing_task.refresh_from_db()
        if processing_task.status == DocumentProcessingTask.Status.ENQUEUE_FAILED:
            return Response(
                {
                    "code": 503,
                    "message": "任务队列暂时不可用，请稍后重试",
                    "data": asynchronous_document_payload(document, processing_task),
                },
                status=503,
            )
        return Response(
            {
                "code": 202,
                "message": "文档已进入处理队列",
                "data": asynchronous_document_payload(document, processing_task),
            },
            status=202,
        )


class DocumentDetailView(WorkspaceAPIView):
    def get(self, request, knowledge_id, document_id):
        self.require(request, "knowledge.read")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)
        return ok(DocumentSerializer(document).data)

    def delete(self, request, knowledge_id, document_id):
        self.require(request, "document.process")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)
        for processing_task in document.processing_tasks.filter(
            status__in=DocumentProcessingTask.ACTIVE_STATUSES
        ):
            request_task_cancel(processing_task)
        document.delete()
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="document.delete", resource_type="Document", resource_id=document_id,
        )
        return ok(True, "文档已删除")


class DocumentParagraphListView(WorkspaceAPIView):
    def get(self, request, knowledge_id, document_id):
        self.require(request, "knowledge.read")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)

        try:
            page_size = int(request.query_params.get("page_size", 20))
        except (TypeError, ValueError):
            page_size = 20
        page_size = max(1, min(page_size, 100))

        paragraphs = document.paragraphs.filter(
            chunk_type__in=(Paragraph.ChunkType.LEGACY, Paragraph.ChunkType.CHILD)
        ).select_related("parent_section")
        paginator = Paginator(paragraphs, page_size)
        page = paginator.get_page(request.query_params.get("page", 1))
        return ok(
            {
                "items": ParagraphSerializer(page.object_list, many=True).data,
                "total": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages if paginator.count else 0,
            }
        )


class DocumentReprocessView(WorkspaceAPIView):
    def post(self, request, knowledge_id, document_id):
        self.require(request, "document.process")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if len(idempotency_key) > 100:
            return Response({"code": 400, "message": "幂等键不能超过100个字符", "data": None}, status=400)
        processing_task, _ = create_processing_task(
            document,
            DocumentProcessingTask.TaskType.REPROCESS,
            idempotency_key=idempotency_key,
        )
        if processing_task.status == DocumentProcessingTask.Status.PENDING and not processing_task.celery_task_id:
            processing_task = dispatch_processing_task(processing_task.id).processing_task
        document.refresh_from_db()
        processing_task.refresh_from_db()
        response_status = 503 if processing_task.status == DocumentProcessingTask.Status.ENQUEUE_FAILED else 202
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="document.reprocess", resource_type="Document", resource_id=document_id,
            result="FAILURE" if response_status == 503 else "SUCCESS",
            metadata={"status": processing_task.status},
        )
        return Response(
            {
                "code": response_status,
                "message": "任务队列暂时不可用，请稍后重试"
                if response_status == 503
                else "文档重新处理任务已进入队列",
                "data": asynchronous_document_payload(document, processing_task),
            },
            status=response_status,
        )


class DocumentChunkingConfigView(WorkspaceAPIView):
    def get(self, request, knowledge_id, document_id):
        self.require(request, "knowledge.read")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)
        return ok(DocumentChunkingConfigSerializer(document).data)

    def patch(self, request, knowledge_id, document_id):
        self.require(request, "document.process")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)
        if document.processing_tasks.filter(
            status__in=DocumentProcessingTask.ACTIVE_STATUSES
        ).exists():
            return Response(
                {"code": 409, "message": "文档正在处理，完成后再修改切片配置", "data": None},
                status=409,
            )
        serializer = DocumentChunkingConfigSerializer(
            document,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return validation_error_response(serializer)
        document = serializer.save()
        return ok(
            {
                "config": DocumentChunkingConfigSerializer(document).data,
                "document": DocumentSerializer(document).data,
            },
            "切片配置已保存，请重新索引文档后生效",
        )


class DocumentChunkPreviewView(WorkspaceAPIView):
    def post(self, request, knowledge_id, document_id):
        self.require(request, "document.process")
        document = get_owned_document(request, knowledge_id, document_id)
        if not document:
            return Response({"code": 404, "message": "文档不存在", "data": None}, status=404)
        serializer = DocumentChunkingConfigSerializer(
            document,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return validation_error_response(serializer)
        try:
            payload = preview_document_chunks(document, serializer.validated_data)
        except (OSError, ValueError) as exc:
            return Response(
                {"code": 400, "message": str(exc)[:500], "data": None},
                status=400,
            )
        return ok(payload)


class DocumentProcessingTaskListView(WorkspaceAPIView):
    def get(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=knowledge_id, workspace=self.workspace(request)
        ).first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        queryset = owned_processing_tasks(request, knowledge_id)
        task_status = request.query_params.get("status", "").strip().upper()
        if task_status:
            if task_status == "ACTIVE":
                queryset = queryset.filter(status__in=DocumentProcessingTask.ACTIVE_STATUSES)
            elif task_status in DocumentProcessingTask.Status.values:
                queryset = queryset.filter(status=task_status)
            else:
                return Response({"code": 400, "message": "任务状态无效", "data": None}, status=400)
        try:
            page_size = int(request.query_params.get("page_size", 20))
        except (TypeError, ValueError):
            page_size = 20
        page_size = max(1, min(page_size, 100))
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(request.query_params.get("page", 1))
        return ok(
            {
                "items": DocumentProcessingTaskListSerializer(page.object_list, many=True).data,
                "total": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages if paginator.count else 0,
            }
        )


class DocumentProcessingTaskDetailView(WorkspaceAPIView):
    def get_task(self, request, knowledge_id, task_id):
        return owned_processing_tasks(request, knowledge_id).filter(pk=task_id).first()

    def get(self, request, knowledge_id, task_id):
        self.require(request, "knowledge.read")
        processing_task = self.get_task(request, knowledge_id, task_id)
        if not processing_task:
            return Response({"code": 404, "message": "处理任务不存在", "data": None}, status=404)
        return ok(DocumentProcessingTaskDetailSerializer(processing_task).data)


class DocumentProcessingTaskRetryView(DocumentProcessingTaskDetailView):
    def post(self, request, knowledge_id, task_id):
        self.require(request, "document.process")
        processing_task = self.get_task(request, knowledge_id, task_id)
        if not processing_task:
            return Response({"code": 404, "message": "处理任务不存在", "data": None}, status=404)
        if processing_task.status not in {
            DocumentProcessingTask.Status.FAILURE,
            DocumentProcessingTask.Status.ENQUEUE_FAILED,
            DocumentProcessingTask.Status.CANCELLED,
        }:
            return Response({"code": 409, "message": "当前任务状态不能重试", "data": None}, status=409)
        retry_task = retry_processing_task(processing_task)
        retry_task.refresh_from_db()
        response_status = 503 if retry_task.status == DocumentProcessingTask.Status.ENQUEUE_FAILED else 202
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="document.task.retry", resource_type="DocumentProcessingTask", resource_id=task_id,
            result="FAILURE" if response_status == 503 else "SUCCESS",
            metadata={"status": retry_task.status},
        )
        return Response(
            {
                "code": response_status,
                "message": "任务队列暂时不可用，请稍后重试"
                if response_status == 503
                else "重试任务已进入队列",
                "data": DocumentProcessingTaskDetailSerializer(retry_task).data,
            },
            status=response_status,
        )


class DocumentProcessingTaskCancelView(DocumentProcessingTaskDetailView):
    def post(self, request, knowledge_id, task_id):
        self.require(request, "document.process")
        processing_task = self.get_task(request, knowledge_id, task_id)
        if not processing_task:
            return Response({"code": 404, "message": "处理任务不存在", "data": None}, status=404)
        if processing_task.status not in {
            *DocumentProcessingTask.ACTIVE_STATUSES,
            DocumentProcessingTask.Status.ENQUEUE_FAILED,
        }:
            return Response({"code": 409, "message": "当前任务已经结束", "data": None}, status=409)
        processing_task = request_task_cancel(processing_task)
        processing_task.refresh_from_db()
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="document.task.cancel", resource_type="DocumentProcessingTask", resource_id=task_id,
            metadata={"status": processing_task.status},
        )
        return ok(DocumentProcessingTaskDetailSerializer(processing_task).data, "取消请求已提交")


class ModelConfigListView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "model.read")
        queryset = ModelConfig.objects.filter(workspace=self.workspace(request))
        model_type = request.query_params.get("model_type", "").strip().upper()
        if model_type:
            if model_type not in ModelConfig.ModelType.values:
                return Response(
                    {"code": 400, "message": "模型类型无效", "data": None},
                    status=400,
                )
            queryset = queryset.filter(model_type=model_type)
        try:
            page_size = int(request.query_params.get("page_size", 20))
        except (TypeError, ValueError):
            page_size = 20
        page_size = max(1, min(page_size, 100))
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(request.query_params.get("page", 1))
        return ok(
            {
                "items": ModelConfigReadSerializer(page.object_list, many=True).data,
                "total": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages if paginator.count else 0,
            }
        )

    def post(self, request):
        access = self.require(request, "model.manage")
        serializer = ModelConfigWriteSerializer(
            data=request.data, context={"request": request, "workspace": access.workspace}
        )
        if not serializer.is_valid():
            return validation_error_response(serializer)
        config = serializer.save()
        record_audit_event(
            request, organization=access.workspace.organization, workspace=access.workspace,
            action="model_config.create", resource_type="ModelConfig", resource_id=config.pk,
        )
        return ok(ModelConfigReadSerializer(config).data, "模型配置创建成功")


class ModelConfigDetailView(WorkspaceAPIView):
    def get_config(self, request, config_id):
        return ModelConfig.objects.filter(pk=config_id, workspace=self.workspace(request)).first()

    def get(self, request, config_id):
        self.require(request, "model.read")
        config = self.get_config(request, config_id)
        if not config:
            return Response({"code": 404, "message": "模型配置不存在", "data": None}, status=404)
        return ok(ModelConfigReadSerializer(config).data)

    def patch(self, request, config_id):
        access = self.require(request, "model.manage")
        config = self.get_config(request, config_id)
        if not config:
            return Response({"code": 404, "message": "模型配置不存在", "data": None}, status=404)
        serializer = ModelConfigWriteSerializer(
            config,
            data=request.data,
            partial=True,
            context={"request": request, "workspace": access.workspace},
        )
        if not serializer.is_valid():
            return validation_error_response(serializer)
        config = serializer.save()
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="model_config.update", resource_type="ModelConfig", resource_id=config.pk,
            metadata={"changed_fields": [key for key in request.data.keys() if key != "api_key"]},
        )
        return ok(ModelConfigReadSerializer(config).data, "模型配置修改成功")

    def delete(self, request, config_id):
        self.require(request, "model.manage")
        config = self.get_config(request, config_id)
        if not config:
            return Response({"code": 404, "message": "模型配置不存在", "data": None}, status=404)
        workspace = self.workspace(request)
        resource_id = config.pk
        try:
            config.delete()
        except RestrictedError:
            return Response(
                {
                    "code": 409,
                    "message": "模型配置正在被知识库使用，请先解除关联",
                    "data": None,
                },
                status=409,
            )
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="model_config.delete", resource_type="ModelConfig", resource_id=resource_id,
        )
        return ok(True, "模型配置已删除")


class ModelConfigTestView(WorkspaceAPIView):
    def post(self, request, config_id):
        self.require(request, "model.manage")
        config = ModelConfig.objects.filter(
            pk=config_id, workspace=self.workspace(request)
        ).first()
        if not config:
            return Response({"code": 404, "message": "模型配置不存在", "data": None}, status=404)
        try:
            result = test_model_config(config)
        except ModelServiceError as exc:
            workspace = self.workspace(request)
            record_audit_event(
                request, organization=workspace.organization, workspace=workspace,
                action="model_config.test", resource_type="ModelConfig", resource_id=config.pk,
                result="FAILURE", metadata={"reason_code": exc.error_code},
            )
            return Response(
                {
                    "code": 400,
                    "message": exc.message,
                    "data": {"success": False, "error_code": exc.error_code},
                },
                status=400,
            )
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="model_config.test", resource_type="ModelConfig", resource_id=config.pk,
        )
        return ok(result, "模型连接成功")


class KnowledgeBaseModelConfigView(WorkspaceAPIView):
    def get_knowledge_base(self, request, knowledge_id):
        return KnowledgeBase.objects.filter(
            pk=knowledge_id, workspace=self.workspace(request)
        ).select_related(
            "chat_model_config",
            "embedding_model_config",
        ).first()

    def get(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        return ok(model_status(knowledge_base))

    def patch(self, request, knowledge_id):
        access = self.require(request, "knowledge.write")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        serializer = KnowledgeBaseModelSelectionSerializer(
            data=request.data,
            partial=True,
            context={"request": request, "workspace": access.workspace},
        )
        if not serializer.is_valid():
            return validation_error_response(serializer)
        resolved = serializer.validated_data.pop("resolved_configs", {})
        update_fields = []
        if "chat_model_config_id" in resolved:
            knowledge_base.chat_model_config = resolved["chat_model_config_id"]
            update_fields.append("chat_model_config")
        if "embedding_model_config_id" in resolved:
            knowledge_base.embedding_model_config = resolved["embedding_model_config_id"]
            update_fields.append("embedding_model_config")
        if update_fields:
            knowledge_base.save(update_fields=update_fields)
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        return ok(model_status(knowledge_base), "知识库模型配置已更新")


class KnowledgeBaseRetrievalConfigView(WorkspaceAPIView):
    def get_knowledge_base(self, request, knowledge_id):
        return KnowledgeBase.objects.filter(
            pk=knowledge_id, workspace=self.workspace(request)
        ).first()

    def get(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        return ok(RetrievalConfigSerializer(knowledge_base).data)

    def patch(self, request, knowledge_id):
        self.require(request, "knowledge.write")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        serializer = RetrievalConfigSerializer(knowledge_base, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer)
        serializer.save()
        return ok(serializer.data, "检索配置已更新")


class KnowledgeBaseRetrievalDebugView(WorkspaceAPIView):
    def post(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=knowledge_id,
            workspace=self.workspace(request),
        ).select_related("embedding_model_config").first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        serializer = RetrievalDebugRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer)
        try:
            result = retrieve_candidates(
                knowledge_base,
                serializer.validated_data["query"],
            )
        except ModelServiceError as exc:
            return Response(
                {"code": 400, "message": exc.message, "data": {"error_code": exc.error_code}},
                status=400,
            )
        return ok(result.debug_payload(serializer.validated_data["candidate_limit"]))


class RetrievalCapabilitiesView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "knowledge.read")
        return ok(reranker_capabilities())


class KnowledgeBaseRetrievalCompareView(WorkspaceAPIView):
    def post(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=knowledge_id,
            workspace=self.workspace(request),
        ).select_related("embedding_model_config").first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        serializer = RetrievalCompareRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer)

        baseline_settings = resolve_retrieval_settings(knowledge_base)
        experimental = serializer.validated_data["experimental"]
        mapping = {
            "retrieval_mode": "mode",
            "fusion_method": "fusion_method",
            "retrieval_top_k": "top_k",
            "similarity_threshold": "threshold",
            "vector_weight": "vector_weight",
            "vector_candidate_k": "vector_candidate_k",
            "keyword_candidate_k": "keyword_candidate_k",
            "rrf_k": "rrf_k",
            "rerank_enabled": "rerank_enabled",
            "rerank_candidate_k": "rerank_candidate_k",
            "max_context_chars": "max_context_chars",
        }
        replacements = {mapping[key]: value for key, value in experimental.items()}
        if "vector_weight" in replacements:
            replacements["keyword_weight"] = 1 - replacements["vector_weight"]
        experimental_settings = replace(baseline_settings, **replacements)
        if experimental_settings.rerank_enabled and not (
            experimental_settings.mode == KnowledgeBase.RetrievalMode.HYBRID
            and experimental_settings.fusion_method == KnowledgeBase.FusionMethod.RRF
        ):
            return Response(
                {"code": 400, "message": "Cross-Encoder重排只能与混合检索的RRF融合一起启用", "data": None},
                status=400,
            )
        if (
            experimental_settings.fusion_method == KnowledgeBase.FusionMethod.RRF
            and min(
                experimental_settings.vector_candidate_k,
                experimental_settings.keyword_candidate_k,
            ) < experimental_settings.top_k
        ):
            return Response({"code": 400, "message": "两路候选数量不能小于最终返回数量", "data": None}, status=400)
        if (
            experimental_settings.rerank_enabled
            and experimental_settings.rerank_candidate_k < experimental_settings.top_k
        ):
            return Response({"code": 400, "message": "重排候选数量不能小于最终返回数量", "data": None}, status=400)
        if experimental_settings.rerank_enabled and experimental_settings.rerank_candidate_k > (
            experimental_settings.vector_candidate_k
            + experimental_settings.keyword_candidate_k
        ):
            return Response({"code": 400, "message": "重排候选数量不能超过两路召回候选数量之和", "data": None}, status=400)

        try:
            prepared = prepare_retrieval(
                knowledge_base,
                serializer.validated_data["query"],
            )
            baseline = retrieve_from_prepared(prepared, settings=baseline_settings)
            experiment = retrieve_from_prepared(prepared, settings=experimental_settings)
        except ModelServiceError as exc:
            return Response(
                {"code": 400, "message": exc.message, "data": {"error_code": exc.error_code}},
                status=400,
            )

        baseline_ranks = {
            item.paragraph_id: item.final_rank
            for item in baseline.candidates
            if item.included and item.final_rank is not None
        }
        experiment_ranks = {
            item.paragraph_id: item.final_rank
            for item in experiment.candidates
            if item.included and item.final_rank is not None
        }
        item_ids = set(baseline_ranks) | set(experiment_ranks)
        changes = [
            {
                "paragraph_id": item_id,
                "baseline_rank": baseline_ranks.get(item_id),
                "experimental_rank": experiment_ranks.get(item_id),
                "rank_change": (
                    baseline_ranks[item_id] - experiment_ranks[item_id]
                    if item_id in baseline_ranks and item_id in experiment_ranks
                    else None
                ),
                "change_type": (
                    "ADDED" if item_id not in baseline_ranks
                    else "REMOVED" if item_id not in experiment_ranks
                    else "MOVED" if baseline_ranks[item_id] != experiment_ranks[item_id]
                    else "UNCHANGED"
                ),
            }
            for item_id in sorted(
                item_ids,
                key=lambda value: (
                    experiment_ranks.get(value, 10**9),
                    baseline_ranks.get(value, 10**9),
                    value,
                ),
            )
        ]
        limit = serializer.validated_data["candidate_limit"]
        return ok(
            {
                "query": serializer.validated_data["query"],
                "baseline": baseline.debug_payload(limit),
                "experimental": experiment.debug_payload(limit),
                "changes": changes[:limit],
            }
        )


class KnowledgeBaseAgentConfigView(WorkspaceAPIView):
    def get_knowledge_base(self, request, knowledge_id):
        return KnowledgeBase.objects.filter(
            pk=knowledge_id, workspace=self.workspace(request)
        ).select_related(
            "chat_model_config"
        ).first()

    def get(self, request, knowledge_id):
        self.require(request, "knowledge.read")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        return ok(agent_config_payload(knowledge_base))

    def patch(self, request, knowledge_id):
        self.require(request, "knowledge.write")
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        serializer = AgentConfigSerializer(knowledge_base, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer)
        serializer.save()
        knowledge_base = self.get_knowledge_base(request, knowledge_id)
        return ok(agent_config_payload(knowledge_base), "Agent配置已更新")


class AgentRunDetailView(WorkspaceAPIView):
    def get(self, request, knowledge_id, agent_run_id):
        self.require(request, "knowledge.read")
        agent_run = (
            AgentRun.objects.filter(
                pk=agent_run_id,
                conversation__knowledge_base_id=knowledge_id,
                conversation__knowledge_base__workspace=self.workspace(request),
                conversation__owner=request.user,
            )
            .select_related("user_message", "assistant_message", "conversation")
            .prefetch_related(
                Prefetch(
                    "tool_executions",
                    queryset=ToolExecution.objects.order_by("step", "sequence", "id"),
                )
            )
            .first()
        )
        if not agent_run:
            return Response({"code": 404, "message": "Agent执行记录不存在", "data": None}, status=404)
        return ok(AgentRunSerializer(agent_run).data)


class SearchView(WorkspaceAPIView):
    def post(self, request, pk):
        self.require(request, "knowledge.read")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=pk, workspace=self.workspace(request)
        ).select_related(
            "embedding_model_config"
        ).first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        query = str(request.data.get("query", "")).strip()
        if not query:
            return Response({"code": 400, "message": "问题不能为空", "data": None}, status=400)
        top_k = request.data.get("top_k")
        if top_k is not None:
            try:
                top_k = int(top_k)
            except (TypeError, ValueError):
                return Response({"code": 400, "message": "top_k必须是整数", "data": None}, status=400)
            if top_k < 1 or top_k > 20:
                return Response({"code": 400, "message": "top_k必须在1到20之间", "data": None}, status=400)
        try:
            results = search_paragraphs(knowledge_base, query, top_k)
        except ModelServiceError as exc:
            return Response(
                {"code": 400, "message": exc.message, "data": {"error_code": exc.error_code}},
                status=400,
            )
        return ok(results)


class ConversationListView(WorkspaceAPIView):
    def get(self, request, pk):
        self.require(request, "knowledge.chat")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=pk, workspace=self.workspace(request)
        ).first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        try:
            page_size = int(request.query_params.get("page_size", 20))
        except (TypeError, ValueError):
            page_size = 20
        page_size = max(1, min(page_size, 100))

        conversations = owned_conversation_queryset(request, pk).order_by("-last_message_at", "-id")
        paginator = Paginator(conversations, page_size)
        page = paginator.get_page(request.query_params.get("page", 1))
        return ok(
            {
                "items": ConversationListSerializer(page.object_list, many=True).data,
                "total": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages if paginator.count else 0,
            }
        )


class ConversationDetailView(WorkspaceAPIView):
    def get(self, request, knowledge_id, conversation_id):
        self.require(request, "knowledge.chat")
        conversation = get_owned_conversation(request, knowledge_id, conversation_id)
        if not conversation:
            return Response({"code": 404, "message": "会话不存在", "data": None}, status=404)
        return ok(ConversationDetailSerializer(conversation).data)

    def patch(self, request, knowledge_id, conversation_id):
        self.require(request, "knowledge.chat")
        conversation = get_owned_conversation(request, knowledge_id, conversation_id)
        if not conversation:
            return Response({"code": 404, "message": "会话不存在", "data": None}, status=404)
        serializer = ConversationTitleSerializer(conversation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        conversation = get_owned_conversation(request, knowledge_id, conversation_id)
        return ok(ConversationDetailSerializer(conversation).data, "会话标题已修改")

    def delete(self, request, knowledge_id, conversation_id):
        self.require(request, "knowledge.chat")
        conversation = get_owned_conversation(request, knowledge_id, conversation_id)
        if not conversation:
            return Response({"code": 404, "message": "会话不存在", "data": None}, status=404)
        conversation.delete()
        return ok(True, "会话已删除")


class ConversationMessageListView(WorkspaceAPIView):
    def get(self, request, knowledge_id, conversation_id):
        self.require(request, "knowledge.chat")
        conversation = get_owned_conversation(request, knowledge_id, conversation_id)
        if not conversation:
            return Response({"code": 404, "message": "会话不存在", "data": None}, status=404)

        try:
            page_size = int(request.query_params.get("page_size", 50))
        except (TypeError, ValueError):
            page_size = 50
        page_size = max(1, min(page_size, 100))

        messages = (
            conversation.messages.order_by("created_at", "id")
            .select_related("agent_run_as_user", "agent_run_as_assistant")
            .prefetch_related(
                Prefetch(
                    "agent_run_as_user__tool_executions",
                    queryset=ToolExecution.objects.order_by("step", "sequence", "id"),
                ),
                Prefetch(
                    "agent_run_as_assistant__tool_executions",
                    queryset=ToolExecution.objects.order_by("step", "sequence", "id"),
                ),
            )
        )
        paginator = Paginator(messages, page_size)
        requested_page = request.query_params.get("page", "last")
        if requested_page == "last":
            requested_page = paginator.num_pages or 1
        page = paginator.get_page(requested_page)
        return ok(
            {
                "items": MessageSerializer(page.object_list, many=True).data,
                "total": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages if paginator.count else 0,
                "has_previous": page.has_previous(),
                "previous_page": page.previous_page_number() if page.has_previous() else None,
            }
        )


class ChatStreamView(WorkspaceAPIView):
    def post(self, request, pk):
        self.require(request, "knowledge.chat")
        knowledge_base = KnowledgeBase.objects.filter(
            pk=pk, workspace=self.workspace(request)
        ).select_related(
            "chat_model_config",
            "embedding_model_config",
        ).first()
        if not knowledge_base:
            return Response({"code": 404, "message": "知识库不存在", "data": None}, status=404)
        question = str(request.data.get("message", "")).strip()
        if not question:
            return Response({"code": 400, "message": "问题不能为空", "data": None}, status=400)
        conversation_id = request.data.get("conversation_id")
        if conversation_id is None:
            conversation = None
        else:
            conversation = get_owned_conversation(request, pk, conversation_id)
            if not conversation:
                return Response({"code": 404, "message": "会话不存在", "data": None}, status=404)

        conversation, user_message = record_user_question_with_message(
            knowledge_base,
            request.user,
            question,
            conversation,
        )
        if knowledge_base.agent_enabled:
            agent_run = create_agent_run(conversation, user_message)

            def agent_event_stream():
                yield sse("meta", {"conversation_id": conversation.id})
                for event in stream_agent_run(agent_run):
                    yield sse(event["event"], event["data"])

            response = StreamingHttpResponse(agent_event_stream(), content_type="text/event-stream")
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response

        try:
            references = search_paragraphs(knowledge_base, question)
        except Exception as exc:
            message = exc.message if isinstance(exc, ModelServiceError) else "知识检索失败，请稍后重试"
            return Response(
                {
                    "code": 400,
                    "message": message,
                    "data": {"conversation_id": conversation.id},
                },
                status=400,
            )

        def event_stream():
            parts = []
            try:
                yield sse("meta", {"conversation_id": conversation.id})
                for content in stream_answer(knowledge_base, question, references):
                    parts.append(content)
                    yield sse("content", {"content": content})
                answer = "".join(parts)
                visible_references = public_references(references)
                save_assistant_message(conversation, answer, visible_references)
                yield sse("references", visible_references)
                yield sse("done", {})
            except Exception as exc:
                message = exc.message if isinstance(exc, ModelServiceError) else "模型回答生成失败，请稍后重试"
                yield sse("error", {"message": message})

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
