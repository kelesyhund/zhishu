import math
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.core.paginator import Paginator
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Application, ApplicationAccessLog, AuditEvent, Document, DocumentProcessingTask,
    ModelConfig, Organization, OrganizationInvitation, OrganizationMembership,
    WorkspaceMembership,
)
from .serializers import DocumentProcessingTaskDetailSerializer, DocumentProcessingTaskListSerializer, RegisterSerializer
from .services.account_security import ensure_profile, normalize_email, register_account_session
from .services.audit import record_audit_event
from .services.document_tasks import request_task_cancel, retry_processing_task
from .services.invitations import (
    InvitationError, accept_invitation, create_invitation, invitation_payload, resolve_invitation,
)
from .services.model_resolution import document_needs_reprocess
from .services.workspace_permissions import WorkspaceContextError, resolve_workspace_access
from .workspace_api import WorkspaceAPIView
from .workspace_serializers import AuditEventSerializer


def ok(data=None, message="success"):
    return Response({"code": 200, "message": message, "data": data})


def error(message, status_code=400, error_code=""):
    return Response({"code": status_code, "message": message,
                     "data": {"error_code": error_code} if error_code else None}, status=status_code)


def _org_for_manager(request, organization_id):
    organization = Organization.objects.filter(pk=organization_id, memberships__user=request.user).first()
    if not organization:
        raise WorkspaceContextError("组织不存在", 404, "ORGANIZATION_NOT_FOUND")
    role = organization.memberships.filter(user=request.user).values_list("role", flat=True).first()
    if role not in {OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN}:
        raise WorkspaceContextError("当前角色无权管理邀请", 403, "PERMISSION_DENIED")
    return organization


class InvitationAPIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, WorkspaceContextError):
            return error(exc.message, exc.status_code, exc.error_code)
        if isinstance(exc, InvitationError):
            return error(exc.message, exc.status_code, exc.error_code)
        return super().handle_exception(exc)


class OrganizationInvitationListView(InvitationAPIView):
    def get(self, request, organization_id):
        organization = _org_for_manager(request, organization_id)
        invitations = organization.invitations.prefetch_related("workspace_grants__workspace")[:100]
        return ok({"items": [invitation_payload(item) for item in invitations], "total": organization.invitations.count()})

    def post(self, request, organization_id):
        organization = _org_for_manager(request, organization_id)
        email_field = serializers.EmailField()
        try:
            email = normalize_email(email_field.run_validation(request.data.get("email", "")))
        except serializers.ValidationError:
            return error("请输入有效邮箱地址")
        role = str(request.data.get("organization_role", OrganizationMembership.Role.MEMBER)).upper()
        if role not in OrganizationMembership.Role.values or role == OrganizationMembership.Role.OWNER:
            return error("邀请角色无效")
        grants = []
        for item in request.data.get("workspace_grants", []):
            workspace = organization.workspaces.filter(pk=item.get("workspace_id"), status="ACTIVE").first()
            grant_role = str(item.get("role", WorkspaceMembership.Role.VIEWER)).upper()
            if not workspace or grant_role not in WorkspaceMembership.Role.values:
                return error("工作空间授权无效")
            grants.append({"workspace": workspace, "role": grant_role})
        invitation, raw_token = create_invitation(organization, request.user, email, role, grants)
        invitation_url = f"{settings.FRONTEND_BASE_URL}/invite/{raw_token}"
        try:
            send_mail("知枢组织邀请", f"你受邀加入 {organization.name}。请在有效期内打开：\n{invitation_url}",
                      settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True)
        except Exception:
            pass
        record_audit_event(request, organization=organization, action="invitation.create",
                           resource_type="OrganizationInvitation", resource_id=invitation.pk,
                           metadata={"member_role": role})
        return ok({**invitation_payload(invitation),
                   "invitation_url": invitation_url}, "邀请已创建")


class OrganizationInvitationActionView(InvitationAPIView):
    def post(self, request, organization_id, invitation_id, action):
        organization = _org_for_manager(request, organization_id)
        invitation = organization.invitations.filter(pk=invitation_id).first()
        if not invitation:
            return error("邀请不存在", 404)
        if action == "revoke":
            if invitation.status != OrganizationInvitation.Status.PENDING:
                return error("当前邀请不能撤销", 409)
            invitation.status = OrganizationInvitation.Status.REVOKED
            invitation.save(update_fields=["status", "updated_at"])
            record_audit_event(request, organization=organization, action="invitation.revoke",
                               resource_type="OrganizationInvitation", resource_id=invitation.pk)
            return ok(invitation_payload(invitation), "邀请已撤销")
        if action == "resend":
            grants = [{"workspace": grant.workspace, "role": grant.workspace_role}
                      for grant in invitation.workspace_grants.select_related("workspace")]
            new_invitation, token = create_invitation(
                organization, request.user, invitation.email_normalized,
                invitation.organization_role, grants,
            )
            record_audit_event(request, organization=organization, action="invitation.resend",
                               resource_type="OrganizationInvitation", resource_id=new_invitation.pk)
            return ok({**invitation_payload(new_invitation),
                       "invitation_url": f"{settings.FRONTEND_BASE_URL}/invite/{token}"}, "邀请已重新生成")
        return error("操作无效", 404)


class PublicInvitationPreviewView(InvitationAPIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        invitation = resolve_invitation(token)
        data = invitation_payload(invitation)
        return ok({"organization_name": invitation.organization.name,
                   "email_masked": data["email_masked"], "expires_at": invitation.expires_at,
                   "status": invitation.status})


@method_decorator(csrf_protect, name="dispatch")
class PublicInvitationAcceptView(InvitationAPIView):
    def post(self, request, token):
        invitation = accept_invitation(token, request.user)
        record_audit_event(request, organization=invitation.organization, action="invitation.accept",
                           resource_type="OrganizationInvitation", resource_id=invitation.pk)
        return ok({"organization_id": invitation.organization_id}, "已加入组织")


@method_decorator(csrf_protect, name="dispatch")
class PublicInvitationRegisterView(InvitationAPIView):
    permission_classes = [AllowAny]

    def post(self, request, token):
        invitation = resolve_invitation(token)
        payload = {**request.data, "email": invitation.email_normalized}
        serializer = RegisterSerializer(data=payload)
        if not serializer.is_valid():
            return error("注册信息校验失败", 400)
        try:
            with transaction.atomic():
                user = serializer.save()
                profile = ensure_profile(user)
                profile.normalized_email = invitation.email_normalized
                profile.save(update_fields=["normalized_email", "updated_at"])
                invitation = accept_invitation(token, user)
        except IntegrityError:
            return error("用户名或邮箱已被使用")
        login(request, user)
        request.user = user
        register_account_session(request)
        record_audit_event(request, organization=invitation.organization, action="invitation.register_accept",
                           resource_type="OrganizationInvitation", resource_id=invitation.pk)
        return ok({"username": user.username, "organization_id": invitation.organization_id}, "注册并加入组织成功")


class DashboardOverviewView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "workspace.read")
        workspace = self.workspace(request)
        range_name = request.query_params.get("range", "7d")
        if range_name not in {"7d", "30d"}:
            return error("统计范围只支持7d或30d")
        days = 7 if range_name == "7d" else 30
        since = timezone.now() - timedelta(days=days)
        documents = Document.objects.filter(knowledge_base__workspace=workspace)
        tasks = DocumentProcessingTask.objects.filter(document__knowledge_base__workspace=workspace)
        logs = ApplicationAccessLog.objects.filter(application__workspace=workspace, created_at__gte=since)
        ended = logs.exclude(status=ApplicationAccessLog.Status.RUNNING)
        total_calls = ended.count()
        successful_calls = ended.filter(status=ApplicationAccessLog.Status.SUCCESS).count()
        latencies = list(ended.order_by("total_latency_ms").values_list("total_latency_ms", flat=True))
        p95 = latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else 0
        finished_documents = documents.filter(status__in=[Document.Status.SUCCESS, Document.Status.FAILURE])
        document_finished = finished_documents.count()
        document_success = finished_documents.filter(status=Document.Status.SUCCESS).count()
        trend_rows = ended.annotate(day=TruncDate("created_at")).values("day").annotate(
            total=Count("id"),
            success=Count("id", filter=Q(status=ApplicationAccessLog.Status.SUCCESS)),
        ).order_by("day")
        return ok({
            "range": range_name,
            "counts": {
                "knowledge_bases": workspace.knowledge_bases.count(), "documents": documents.count(),
                "documents_success": documents.filter(status=Document.Status.SUCCESS).count(),
                "documents_failure": documents.filter(status=Document.Status.FAILURE).count(),
                "documents_processing": documents.filter(status=Document.Status.PROCESSING).count(),
                "published_applications": workspace.applications.filter(status=Application.Status.PUBLISHED).count(),
                "organization_members": workspace.organization.memberships.count(),
                "available_models": workspace.model_configs.filter(last_test_status=ModelConfig.TestStatus.SUCCESS).count(),
            },
            "application_calls": {"total": total_calls, "success": successful_calls,
                                  "success_rate": round(successful_calls * 100 / total_calls, 2) if total_calls else 0,
                                  "average_latency_ms": round(ended.aggregate(value=Avg("total_latency_ms"))["value"] or 0),
                                  "p95_latency_ms": p95,
                                  "no_answer": ended.filter(error_code="NO_ANSWER").count()},
            "document_processing": {"total": document_finished, "success": document_success,
                                    "success_rate": round(document_success * 100 / document_finished, 2) if document_finished else 0},
            "task_counts": {"active": tasks.filter(status__in=DocumentProcessingTask.ACTIVE_STATUSES).count(),
                            "failure": tasks.filter(status__in=[DocumentProcessingTask.Status.FAILURE,
                                                                DocumentProcessingTask.Status.ENQUEUE_FAILED]).count()},
            "trend": [{"date": str(row["day"]), "total": row["total"], "success": row["success"]}
                      for row in trend_rows],
        })


class DashboardActivityView(WorkspaceAPIView):
    def get(self, request):
        access = self.workspace_access(request)
        if "audit.read" not in access.capabilities:
            return ok({"items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0})
        queryset = AuditEvent.objects.filter(
            organization=access.workspace.organization
        ).filter(Q(workspace=access.workspace) | Q(workspace__isnull=True)).select_related("actor", "workspace")
        return ok(_page(request, queryset, AuditEventSerializer))


class DashboardAttentionView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "workspace.read")
        workspace = self.workspace(request)
        items = []
        for task in DocumentProcessingTask.objects.filter(
            document__knowledge_base__workspace=workspace,
            status__in=[DocumentProcessingTask.Status.FAILURE, DocumentProcessingTask.Status.ENQUEUE_FAILED],
        ).select_related("document", "document__knowledge_base")[:20]:
            items.append({"type": "TASK_FAILURE", "title": task.document.name,
                          "description": task.error_message or "文档任务处理失败", "resource_id": task.id})
        for model in workspace.model_configs.filter(last_test_status__in=[ModelConfig.TestStatus.FAILURE, ModelConfig.TestStatus.UNTESTED])[:20]:
            items.append({"type": "MODEL_ATTENTION", "title": model.name,
                          "description": "模型连接失败" if model.last_test_status == "FAILURE" else "模型尚未测试",
                          "resource_id": model.id})
        stale = [doc for doc in Document.objects.filter(knowledge_base__workspace=workspace).select_related(
            "knowledge_base__embedding_model_config") if document_needs_reprocess(doc)]
        for doc in stale[:20]:
            items.append({"type": "DOCUMENT_REPROCESS", "title": doc.name,
                          "description": "向量配置已变化，需要重新处理", "resource_id": doc.id})
        profile = ensure_profile(request.user)
        if not profile.email_verified_at:
            items.insert(0, {"type": "EMAIL_UNVERIFIED", "title": "邮箱尚未验证",
                             "description": "验证邮箱后可安全找回密码", "resource_id": request.user.id})
        soon = timezone.now() + timedelta(days=2)
        for invitation in workspace.organization.invitations.filter(
            status=OrganizationInvitation.Status.PENDING, expires_at__lte=soon
        )[:10]:
            items.append({"type": "INVITATION_EXPIRING", "title": "成员邀请即将过期",
                          "description": "请重新发送或撤销未处理邀请", "resource_id": invitation.id})
        return ok({"items": items[:50], "total": len(items[:50])})


def _page(request, queryset, serializer):
    try:
        size = max(1, min(int(request.query_params.get("page_size", 20)), 100))
    except (TypeError, ValueError):
        size = 20
    paginator = Paginator(queryset, size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return {"items": serializer(page.object_list, many=True).data, "total": paginator.count,
            "page": page.number, "page_size": size,
            "total_pages": paginator.num_pages if paginator.count else 0}


def _task_queryset(request):
    workspace = resolve_workspace_access(request).workspace
    return DocumentProcessingTask.objects.filter(
        document__knowledge_base__workspace=workspace
    ).select_related("document", "document__knowledge_base")


class GlobalTaskListView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "knowledge.read")
        queryset = _task_queryset(request)
        status_value = request.query_params.get("status", "").upper()
        task_type = request.query_params.get("task_type", "").upper()
        keyword = request.query_params.get("keyword", "").strip()
        if status_value:
            if status_value == "ACTIVE":
                queryset = queryset.filter(status__in=DocumentProcessingTask.ACTIVE_STATUSES)
            elif status_value in DocumentProcessingTask.Status.values:
                queryset = queryset.filter(status=status_value)
            else:
                return error("任务状态无效")
        if task_type:
            if task_type not in DocumentProcessingTask.TaskType.values:
                return error("任务类型无效")
            queryset = queryset.filter(task_type=task_type)
        if keyword:
            queryset = queryset.filter(Q(document__name__icontains=keyword) | Q(document__knowledge_base__name__icontains=keyword))
        payload = _page(request, queryset, DocumentProcessingTaskListSerializer)
        kb_by_id = {item.id: item.document.knowledge_base for item in queryset.filter(id__in=[row["id"] for row in payload["items"]])}
        for row in payload["items"]:
            kb = kb_by_id.get(row["id"])
            row["knowledge_base_id"] = kb.id if kb else None
            row["knowledge_base_name"] = kb.name if kb else ""
        return ok(payload)


class GlobalTaskDetailView(WorkspaceAPIView):
    def get_object(self, request, task_id):
        return _task_queryset(request).filter(pk=task_id).first()

    def get(self, request, task_id):
        self.require(request, "knowledge.read")
        task = self.get_object(request, task_id)
        return ok(DocumentProcessingTaskDetailSerializer(task).data) if task else error("处理任务不存在", 404)


class GlobalTaskActionView(GlobalTaskDetailView):
    def post(self, request, task_id, action):
        self.require(request, "document.process")
        task = self.get_object(request, task_id)
        if not task:
            return error("处理任务不存在", 404)
        if action == "retry":
            if task.status not in {DocumentProcessingTask.Status.FAILURE, DocumentProcessingTask.Status.ENQUEUE_FAILED, DocumentProcessingTask.Status.CANCELLED}:
                return error("当前任务状态不能重试", 409)
            task = retry_processing_task(task)
            return Response({"code": 202, "message": "重试任务已进入队列",
                             "data": DocumentProcessingTaskDetailSerializer(task).data}, status=202)
        if action == "cancel":
            if task.status not in {*DocumentProcessingTask.ACTIVE_STATUSES, DocumentProcessingTask.Status.ENQUEUE_FAILED}:
                return error("当前任务已经结束", 409)
            return ok(DocumentProcessingTaskDetailSerializer(request_task_cancel(task)).data, "取消请求已提交")
        return error("操作无效", 404)
