import json
from time import perf_counter
from urllib.parse import quote

from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.html import escape
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Application,
    ApplicationAccessLog,
    ApplicationCredential,
    ApplicationKnowledgeBase,
    ApplicationVersion,
    Conversation,
    KnowledgeBase,
    Message,
)
from .serializers import (
    ApplicationAccessLogSerializer,
    ApplicationCredentialSerializer,
    ApplicationCredentialWriteSerializer,
    ApplicationFrameOriginsSerializer,
    ApplicationKnowledgeSelectionSerializer,
    ApplicationPublicAccessSerializer,
    ApplicationReadSerializer,
    ApplicationVersionSerializer,
    ApplicationWriteSerializer,
    MessageSerializer,
)
from .services.application_access_logs import finish_access_log, start_access_log
from .services.application_conversations import (
    get_application_conversation,
    record_application_question,
)
from .services.application_credentials import (
    ApplicationCredentialError,
    authenticate_application_credential,
    create_application_credential,
)
from .services.application_public_access import (
    PublicAccessError,
    authenticate_public_token,
    issue_visitor_token,
    rotate_public_token,
    verify_visitor_token,
)
from .services.application_publishing import (
    ApplicationPublishingError,
    publish_application,
    rollback_application,
)
from .services.application_rate_limit import RateLimitExceeded, enforce_rate_limit, safe_limit_key
from .services.application_retrieval import retrieve_application_context
from .services.application_runtime import (
    ApplicationRuntimeError,
    resolve_draft_runtime,
    resolve_published_runtime,
)
from .services.agent_executor import stream_agent_run
from .services.agent_persistence import create_agent_run
from .services.conversations import public_references, save_assistant_message
from .services.model_clients import ModelServiceError
from .services.rag import sse, stream_answer
from .services.audit import record_audit_event
from .workspace_api import WorkspaceAPIView


def ok(data=None, message="success"):
    return Response({"code": 200, "message": message, "data": data})


def error(message, status_code=400, data=None):
    return Response({"code": status_code, "message": message, "data": data}, status=status_code)


def validation_error(serializer):
    def first(value):
        if isinstance(value, dict):
            for item in value.values():
                found = first(item)
                if found:
                    return found
        if isinstance(value, (list, tuple)):
            for item in value:
                found = first(item)
                if found:
                    return found
        if value:
            return str(value)
        return ""

    return error(first(serializer.errors) or "请求参数校验失败", 400, serializer.errors)


def owned_application(request, application_id):
    workspace = request.workspace_access.workspace
    return Application.objects.filter(pk=application_id, workspace=workspace).select_related(
        "chat_model_config", "current_published_version"
    ).first()


def application_payload(application):
    application.version_count = application.versions.count()
    return ApplicationReadSerializer(application).data


def page_size(request, default=20, maximum=100):
    try:
        return max(1, min(int(request.query_params.get("page_size", default)), maximum))
    except (TypeError, ValueError):
        return default


def paginated(request, queryset, serializer, default=20, maximum=100):
    size = page_size(request, default, maximum)
    paginator = Paginator(queryset, size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return {
        "items": serializer(page.object_list, many=True).data,
        "total": paginator.count,
        "page": page.number,
        "page_size": size,
        "total_pages": paginator.num_pages if paginator.count else 0,
    }


class ApplicationListView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "application.read")
        queryset = Application.objects.filter(workspace=self.workspace(request)).select_related(
            "chat_model_config", "current_published_version"
        ).prefetch_related("knowledge_links__knowledge_base").annotate(version_count=Count("versions"))
        keyword = request.query_params.get("search", "").strip()
        status_value = request.query_params.get("status", "").strip()
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        if status_value in Application.Status.values:
            queryset = queryset.filter(status=status_value)
        queryset = queryset.order_by("-updated_at", "-id")
        return ok(paginated(request, queryset, ApplicationReadSerializer))

    def post(self, request):
        access = self.require(request, "application.write")
        serializer = ApplicationWriteSerializer(
            data=request.data, context={"request": request, "workspace": access.workspace}
        )
        if not serializer.is_valid():
            return validation_error(serializer)
        application = serializer.save()
        record_audit_event(
            request, organization=access.workspace.organization, workspace=access.workspace,
            action="application.create", resource_type="Application", resource_id=application.pk,
        )
        return ok(application_payload(application), "应用创建成功")


class ApplicationDetailView(WorkspaceAPIView):
    def get(self, request, application_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        return ok(application_payload(application)) if application else error("应用不存在", 404)

    def patch(self, request, application_id):
        access = self.require(request, "application.write")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        serializer = ApplicationWriteSerializer(
            application,
            data=request.data,
            partial=True,
            context={"request": request, "workspace": access.workspace},
        )
        if not serializer.is_valid():
            return validation_error(serializer)
        application = serializer.save()
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.update", resource_type="Application", resource_id=application.pk,
            metadata={"changed_fields": list(request.data.keys())},
        )
        return ok(application_payload(application), "草稿保存成功")

    def delete(self, request, application_id):
        self.require(request, "application.write")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        if application.status == Application.Status.PUBLISHED:
            return error("已发布应用必须先停用才能删除", 409)
        workspace = self.workspace(request)
        resource_id = application.pk
        application.delete()
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.delete", resource_type="Application", resource_id=resource_id,
        )
        return ok(message="应用已删除，关联知识库和模型配置已保留")


class ApplicationKnowledgeBaseView(WorkspaceAPIView):
    def get(self, request, application_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        return ok(application_payload(application)["knowledge_bases"])

    def put(self, request, application_id):
        access = self.require(request, "application.write")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        serializer = ApplicationKnowledgeSelectionSerializer(
            data=request.data, context={"request": request, "workspace": access.workspace}
        )
        if not serializer.is_valid():
            return validation_error(serializer)
        items = serializer.validated_data["knowledge_bases"]
        knowledge_by_id = {
            item.id: item
            for item in KnowledgeBase.objects.filter(
                id__in=[value["knowledge_base_id"] for value in items], workspace=access.workspace
            )
        }
        with transaction.atomic():
            application.knowledge_links.all().delete()
            ApplicationKnowledgeBase.objects.bulk_create(
                [
                    ApplicationKnowledgeBase(
                        application=application,
                        knowledge_base=knowledge_by_id[item["knowledge_base_id"]],
                        position=item["position"],
                        weight=item["weight"],
                        enabled=item["enabled"],
                    )
                    for item in items
                ]
            )
        return ok(application_payload(application)["knowledge_bases"], "知识库绑定已更新")


class ApplicationPublishView(WorkspaceAPIView):
    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        try:
            version = publish_application(application, request.user)
        except ApplicationPublishingError as exc:
            return error(exc.message, exc.status_code, {"error_code": exc.error_code})
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.publish", resource_type="Application", resource_id=application.pk,
        )
        return ok(ApplicationVersionSerializer(version).data, "应用发布成功")


class ApplicationDisableView(WorkspaceAPIView):
    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        application.status = Application.Status.DISABLED
        application.save(update_fields=["status", "updated_at"])
        if hasattr(application, "public_access"):
            application.public_access.enabled = False
            application.public_access.save(update_fields=["enabled"])
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.disable", resource_type="Application", resource_id=application.pk,
        )
        return ok(application_payload(application), "应用已停用")


class ApplicationVersionListView(WorkspaceAPIView):
    def get(self, request, application_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        return ok(paginated(request, application.versions.all(), ApplicationVersionSerializer))


class ApplicationVersionDetailView(WorkspaceAPIView):
    def get(self, request, application_id, version_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        version = ApplicationVersion.objects.filter(pk=version_id, application=application).first()
        return ok(ApplicationVersionSerializer(version).data) if version else error("发布版本不存在", 404)


class ApplicationRollbackView(WorkspaceAPIView):
    def post(self, request, application_id, version_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        version = ApplicationVersion.objects.filter(pk=version_id, application=application).first()
        if not version:
            return error("发布版本不存在", 404)
        try:
            rollback_application(application, version, request.user)
        except ApplicationPublishingError as exc:
            return error(exc.message, exc.status_code)
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.rollback", resource_type="ApplicationVersion", resource_id=version.pk,
        )
        return ok(ApplicationVersionSerializer(version).data, "版本回滚成功")


class ApplicationCredentialListView(WorkspaceAPIView):
    def get(self, request, application_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        return ok(ApplicationCredentialSerializer(application.credentials.all(), many=True).data)

    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        serializer = ApplicationCredentialWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer)
        expires_at = serializer.validated_data.get("expires_at")
        if expires_at and expires_at <= timezone.now():
            return error("过期时间必须晚于当前时间", 400)
        credential, token = create_application_credential(
            application, serializer.validated_data["name"], expires_at
        )
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.credential.create", resource_type="ApplicationCredential",
            resource_id=credential.pk,
        )
        return ok(
            {**ApplicationCredentialSerializer(credential).data, "api_key": token},
            "API Key已创建，请立即复制，关闭后无法再次查看",
        )


class ApplicationCredentialDetailView(WorkspaceAPIView):
    def patch(self, request, application_id, credential_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        credential = ApplicationCredential.objects.filter(
            pk=credential_id, application=application
        ).first()
        if not credential:
            return error("访问凭证不存在", 404)
        if "enabled" in request.data:
            if not isinstance(request.data["enabled"], bool):
                return error("enabled必须是布尔值", 400)
            credential.enabled = request.data["enabled"]
        if "name" in request.data:
            name = str(request.data["name"]).strip()
            if not name:
                return error("凭证名称不能为空", 400)
            credential.name = name[:100]
        credential.save(update_fields=["enabled", "name"])
        workspace = self.workspace(request)
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.credential.update", resource_type="ApplicationCredential",
            resource_id=credential.pk, metadata={"changed_fields": list(request.data.keys())},
        )
        return ok(ApplicationCredentialSerializer(credential).data)

    def delete(self, request, application_id, credential_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        credential = ApplicationCredential.objects.filter(
            pk=credential_id, application=application
        ).first()
        if not credential:
            return error("访问凭证不存在", 404)
        workspace = self.workspace(request)
        resource_id = credential.pk
        credential.delete()
        record_audit_event(
            request, organization=workspace.organization, workspace=workspace,
            action="application.credential.delete", resource_type="ApplicationCredential",
            resource_id=resource_id,
        )
        return ok(message="访问凭证已删除")


class ApplicationPublicAccessView(WorkspaceAPIView):
    def get(self, request, application_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        access = getattr(application, "public_access", None)
        return ok(ApplicationPublicAccessSerializer(access).data if access else {"enabled": False})

    def patch(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        access = getattr(application, "public_access", None) if application else None
        if not access:
            return error("请先启用公开访问", 404)
        serializer = ApplicationFrameOriginsSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer)
        access.allowed_frame_origins = serializer.validated_data["allowed_frame_origins"]
        access.save(update_fields=["allowed_frame_origins"])
        return ok(ApplicationPublicAccessSerializer(access).data)


class ApplicationPublicAccessEnableView(WorkspaceAPIView):
    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        if application.status != Application.Status.PUBLISHED:
            return error("只有已发布应用可以开启公开访问", 409)
        access = getattr(application, "public_access", None)
        token = None
        if access:
            access.enabled = True
            access.save(update_fields=["enabled"])
        else:
            access, token = rotate_public_token(application, enabled=True)
        payload = ApplicationPublicAccessSerializer(access).data
        if token:
            payload["public_token"] = token
        return ok(payload, "公开访问已启用" + ("，请立即复制链接Token" if token else ""))


class ApplicationPublicAccessRotateView(WorkspaceAPIView):
    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        if application.status != Application.Status.PUBLISHED:
            return error("只有已发布应用可以生成公开链接", 409)
        access, token = rotate_public_token(application, enabled=True)
        return ok(
            {**ApplicationPublicAccessSerializer(access).data, "public_token": token},
            "公开Token已轮换，旧链接立即失效，请立即复制新Token",
        )


class ApplicationPublicAccessDisableView(WorkspaceAPIView):
    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        access = getattr(application, "public_access", None) if application else None
        if not access:
            return error("公开访问配置不存在", 404)
        access.enabled = False
        access.save(update_fields=["enabled"])
        return ok(ApplicationPublicAccessSerializer(access).data, "公开访问已关闭")


class ApplicationAccessLogListView(WorkspaceAPIView):
    def get(self, request, application_id):
        self.require(request, "application.read")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        queryset = application.access_logs.select_related("application_version", "credential")
        access_type = request.query_params.get("access_type", "")
        status_value = request.query_params.get("status", "")
        if access_type in Conversation.AccessType.values:
            queryset = queryset.filter(access_type=access_type)
        if status_value in ApplicationAccessLog.Status.values:
            queryset = queryset.filter(status=status_value)
        return ok(paginated(request, queryset, ApplicationAccessLogSerializer))


def _question(data):
    value = str(data.get("message", "")).strip()
    if not value and isinstance(data.get("messages"), list):
        for item in reversed(data["messages"]):
            if isinstance(item, dict) and item.get("role") == "user":
                value = str(item.get("content", "")).strip()
                break
    if not value:
        raise ValueError("问题不能为空")
    if len(value) > 2000:
        raise ValueError("问题不能超过2000个字符")
    return value


def _application_stream(request, runtime, question, conversation, owner, visitor_hash, access_type, credential=None):
    log, started = start_access_log(
        runtime.application, runtime.version, access_type, request, credential=credential
    )
    conversation, _ = record_application_question(
        runtime,
        question,
        conversation=conversation,
        owner=owner,
        visitor_id_hash=visitor_hash,
        access_type=access_type,
    )
    try:
        retrieval = retrieve_application_context(runtime, question)
    except Exception as exc:
        finish_access_log(
            log,
            started,
            status=ApplicationAccessLog.Status.FAILURE,
            status_code=400,
            error_code=getattr(exc, "error_code", "RETRIEVAL_FAILED"),
            conversation=conversation,
        )
        raise

    def events():
        parts = []
        first_token_ms = 0
        stream_started = perf_counter()
        try:
            yield sse("meta", {"conversation_id": conversation.id, "version": runtime.version.version if runtime.version else None})
            for content in stream_answer(runtime.chat_target, question, retrieval.references):
                if not first_token_ms:
                    first_token_ms = round((perf_counter() - stream_started) * 1000)
                parts.append(content)
                yield sse("content", {"content": content})
            answer = "".join(parts)
            visible = public_references(retrieval.references) if runtime.show_references else []
            save_assistant_message(conversation, answer, visible)
            conversation.save(update_fields=["updated_at"])
            yield sse("references", visible)
            yield sse("done", {})
            finish_access_log(
                log,
                started,
                retrieval_latency_ms=retrieval.latency_ms,
                first_token_latency_ms=first_token_ms,
                retrieved_paragraph_count=len(retrieval.references),
                conversation=conversation,
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, ModelServiceError) else "模型回答生成失败，请稍后重试"
            finish_access_log(
                log,
                started,
                status=ApplicationAccessLog.Status.FAILURE,
                status_code=400,
                retrieval_latency_ms=retrieval.latency_ms,
                first_token_latency_ms=first_token_ms,
                retrieved_paragraph_count=len(retrieval.references),
                error_code=getattr(exc, "error_code", "MODEL_FAILED"),
                conversation=conversation,
            )
            yield sse("error", {"message": message, "error_code": getattr(exc, "error_code", "MODEL_FAILED")})

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _application_agent_stream(
    request, runtime, question, conversation, owner, visitor_hash, access_type, credential=None
):
    log, started = start_access_log(
        runtime.application, runtime.version, access_type, request, credential=credential
    )
    conversation, user_message = record_application_question(
        runtime,
        question,
        conversation=conversation,
        owner=owner,
        visitor_id_hash=visitor_hash,
        access_type=access_type,
    )
    agent_run = create_agent_run(conversation, user_message)

    def events():
        finished = False
        first_token_ms = 0
        stream_started = perf_counter()
        try:
            yield sse(
                "meta",
                {
                    "conversation_id": conversation.id,
                    "version": runtime.version.version if runtime.version else None,
                    "agent_run_id": agent_run.id,
                },
            )
            for item in stream_agent_run(agent_run):
                event_name = item["event"]
                data = item["data"]
                if event_name == "content" and not first_token_ms:
                    first_token_ms = round((perf_counter() - stream_started) * 1000)
                if event_name == "done":
                    finish_access_log(
                        log,
                        started,
                        first_token_latency_ms=first_token_ms,
                        conversation=conversation,
                    )
                    finished = True
                elif event_name == "error":
                    finish_access_log(
                        log,
                        started,
                        status=ApplicationAccessLog.Status.FAILURE,
                        status_code=400,
                        first_token_latency_ms=first_token_ms,
                        error_code=data.get("error_code", "AGENT_FAILED"),
                        conversation=conversation,
                    )
                    finished = True
                yield sse(event_name, data)
        finally:
            if not finished:
                finish_access_log(
                    log,
                    started,
                    status=ApplicationAccessLog.Status.FAILURE,
                    status_code=499,
                    first_token_latency_ms=first_token_ms,
                    error_code="CLIENT_DISCONNECTED",
                    conversation=conversation,
                )

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


class ApplicationPreviewChatView(WorkspaceAPIView):
    def post(self, request, application_id):
        self.require(request, "application.operate")
        application = owned_application(request, application_id)
        if not application:
            return error("应用不存在", 404)
        try:
            question = _question(request.data)
            runtime = resolve_draft_runtime(application)
        except (ValueError, ApplicationRuntimeError) as exc:
            return error(getattr(exc, "message", str(exc)), 400)
        conversation_id = request.data.get("conversation_id")
        conversation = None
        if conversation_id is not None:
            conversation = get_application_conversation(
                application,
                conversation_id,
                owner=request.user,
                access_type=Conversation.AccessType.PREVIEW,
            )
            if not conversation:
                return error("会话不存在", 404)
        try:
            stream_factory = _application_agent_stream if runtime.agent_enabled else _application_stream
            return stream_factory(
                request,
                runtime,
                question,
                conversation,
                request.user,
                "",
                Conversation.AccessType.PREVIEW,
            )
        except Exception as exc:
            return error(getattr(exc, "message", "知识检索失败，请稍后重试"), 400)


class PublicApplicationProfileView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public_token):
        try:
            access = authenticate_public_token(public_token)
            runtime = resolve_published_runtime(access.application)
        except (PublicAccessError, ApplicationRuntimeError) as exc:
            return error(exc.message, 404)
        response = ok(
            {
                "name": runtime.name,
                "description": runtime.description,
                "welcome_message": runtime.welcome_message,
                "suggested_questions": runtime.suggested_questions,
                "show_references": runtime.show_references,
                "version": runtime.version.version,
            }
        )
        origins = access.allowed_frame_origins
        response["Content-Security-Policy"] = "frame-ancestors " + (" ".join(origins) if origins else "'self'")
        return response


class PublicApplicationVisitorView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, public_token):
        try:
            access = authenticate_public_token(public_token)
        except PublicAccessError as exc:
            return error(exc.message, 404)
        token, _ = issue_visitor_token(access.application)
        return ok({"visitor_token": token, "expires_in": 86400})


class PublicApplicationEmbedView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public_token):
        try:
            access = authenticate_public_token(public_token)
            resolve_published_runtime(access.application)
        except (PublicAccessError, ApplicationRuntimeError) as exc:
            return error(exc.message, 404)
        frontend_url = settings.PUBLIC_FRONTEND_URL.rstrip("/")
        share_url = f"{frontend_url}/share/{quote(public_token, safe='')}"
        name = escape(access.application.name)
        html = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{name}</title><style>html,body,iframe{{width:100%;height:100%;margin:0;border:0}}</style>
</head><body><iframe src=\"{escape(share_url)}\" title=\"{name}\" referrerpolicy=\"strict-origin-when-cross-origin\"></iframe></body></html>"""
        response = HttpResponse(html, content_type="text/html; charset=utf-8")
        origins = access.allowed_frame_origins
        response["Content-Security-Policy"] = (
            "default-src 'none'; frame-src "
            + escape(frontend_url)
            + "; frame-ancestors "
            + (" ".join(origins) if origins else "'self'")
        )
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PublicApplicationChatView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, public_token):
        try:
            access = authenticate_public_token(public_token)
            runtime = resolve_published_runtime(access.application)
            visitor_hash = verify_visitor_token(
                request.headers.get("X-Visitor-Token", ""), access.application
            )
            enforce_rate_limit(f"public-client:{access.application_id}:{request.META.get('REMOTE_ADDR', '')}", 20)
            enforce_rate_limit(f"application:{access.application_id}", 300)
            question = _question(request.data)
        except RateLimitExceeded as exc:
            response = error(exc.message, 429)
            response["Retry-After"] = str(exc.retry_after)
            return response
        except RuntimeError as exc:
            return error(str(exc), 503)
        except (PublicAccessError, ApplicationRuntimeError) as exc:
            return error(exc.message, 404)
        except ValueError as exc:
            return error(str(exc), 400)
        conversation = None
        conversation_id = request.data.get("conversation_id")
        if conversation_id is not None:
            conversation = get_application_conversation(
                access.application,
                conversation_id,
                visitor_id_hash=visitor_hash,
                access_type=Conversation.AccessType.PUBLIC_WEB,
            )
            if not conversation:
                return error("会话不存在", 404)
        try:
            stream_factory = _application_agent_stream if runtime.agent_enabled else _application_stream
            return stream_factory(
                request,
                runtime,
                question,
                conversation,
                None,
                visitor_hash,
                Conversation.AccessType.PUBLIC_WEB,
            )
        except Exception as exc:
            return error(getattr(exc, "message", "知识检索失败，请稍后重试"), 400)


class PublicApplicationMessageListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public_token, conversation_id):
        try:
            access = authenticate_public_token(public_token)
            visitor_hash = verify_visitor_token(
                request.headers.get("X-Visitor-Token", ""), access.application
            )
        except PublicAccessError as exc:
            return error(exc.message, 404)
        conversation = get_application_conversation(
            access.application,
            conversation_id,
            visitor_id_hash=visitor_hash,
            access_type=Conversation.AccessType.PUBLIC_WEB,
        )
        if not conversation:
            return error("会话不存在", 404)
        messages = conversation.messages.order_by("created_at", "id")
        return ok(paginated(request, messages, MessageSerializer, default=50, maximum=100))


class ApplicationChatCompletionsView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, application_id):
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return error("缺少应用访问凭证", 401)
        try:
            credential = authenticate_application_credential(
                authorization[7:].strip(), application_id
            )
            application = credential.application
            if application.status != Application.Status.PUBLISHED:
                return error("应用未发布或已停用", 409)
            runtime = resolve_published_runtime(application)
            enforce_rate_limit(f"credential:{credential.id}", 60)
            enforce_rate_limit(f"application:{application.id}", 300)
            question = _question(request.data)
        except ApplicationCredentialError as exc:
            return error(exc.message, 401, {"error_code": exc.error_code})
        except RateLimitExceeded as exc:
            response = error(exc.message, 429)
            response["Retry-After"] = str(exc.retry_after)
            return response
        except RuntimeError as exc:
            return error(str(exc), 503)
        except (ApplicationRuntimeError, ValueError) as exc:
            return error(getattr(exc, "message", str(exc)), 400)
        visitor_hash = safe_limit_key("credential-conversation", credential.id)
        conversation = None
        conversation_id = request.data.get("conversation_id")
        if conversation_id is not None:
            conversation = get_application_conversation(
                application,
                conversation_id,
                visitor_id_hash=visitor_hash,
                access_type=Conversation.AccessType.API,
            )
            if not conversation:
                return error("会话不存在", 404)
        if request.data.get("stream", False):
            try:
                stream_factory = _application_agent_stream if runtime.agent_enabled else _application_stream
                return stream_factory(
                    request,
                    runtime,
                    question,
                    conversation,
                    None,
                    visitor_hash,
                    Conversation.AccessType.API,
                    credential,
                )
            except Exception as exc:
                return error(getattr(exc, "message", "知识检索失败，请稍后重试"), 400)

        log, started = start_access_log(
            application, runtime.version, Conversation.AccessType.API, request, credential
        )
        conversation, user_message = record_application_question(
            runtime,
            question,
            conversation=conversation,
            visitor_id_hash=visitor_hash,
            access_type=Conversation.AccessType.API,
        )
        if runtime.agent_enabled:
            agent_run = create_agent_run(conversation, user_message)
            answer_parts = []
            visible = []
            failure = None
            for item in stream_agent_run(agent_run):
                if item["event"] == "content":
                    answer_parts.append(item["data"].get("content", ""))
                elif item["event"] == "references":
                    visible = item["data"] if runtime.show_references else []
                elif item["event"] == "error":
                    failure = item["data"]
            if failure:
                finish_access_log(
                    log,
                    started,
                    status=ApplicationAccessLog.Status.FAILURE,
                    status_code=400,
                    error_code=failure.get("error_code", "AGENT_FAILED"),
                    conversation=conversation,
                )
                return error(failure.get("message", "Agent执行失败，请稍后重试"), 400)
            answer = "".join(answer_parts)
            finish_access_log(log, started, conversation=conversation)
            return Response(
                {
                    "id": f"chatcmpl-{log.request_id}",
                    "object": "chat.completion",
                    "created": int(timezone.now().timestamp()),
                    "model": runtime.chat_target.chat_model_config.model_name if runtime.chat_target.chat_model_config else "system-default",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                    "knowledge_chat": {"conversation_id": conversation.id, "references": visible, "agent_run_id": agent_run.id},
                }
            )
        try:
            retrieval = retrieve_application_context(runtime, question)
            answer = "".join(stream_answer(runtime.chat_target, question, retrieval.references))
            visible = public_references(retrieval.references) if runtime.show_references else []
            save_assistant_message(conversation, answer, visible)
            finish_access_log(
                log,
                started,
                retrieval_latency_ms=retrieval.latency_ms,
                retrieved_paragraph_count=len(retrieval.references),
                conversation=conversation,
            )
        except Exception as exc:
            finish_access_log(
                log,
                started,
                status=ApplicationAccessLog.Status.FAILURE,
                status_code=400,
                error_code=getattr(exc, "error_code", "APPLICATION_CHAT_FAILED"),
                conversation=conversation,
            )
            return error(getattr(exc, "message", "应用问答失败，请稍后重试"), 400)
        return Response(
            {
                "id": f"chatcmpl-{log.request_id}",
                "object": "chat.completion",
                "created": int(timezone.now().timestamp()),
                "model": runtime.chat_target.chat_model_config.model_name if runtime.chat_target.chat_model_config else "local-demo",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "knowledge_chat": {"conversation_id": conversation.id, "references": visible},
            }
        )
