import uuid

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils.text import slugify
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Application,
    AuditEvent,
    KnowledgeBase,
    ModelConfig,
    Organization,
    OrganizationMembership,
    Workspace,
    WorkspaceMembership,
)
from .services.audit import record_audit_event
from .services.workspace_permissions import (
    CAPABILITIES_BY_ROLE,
    WorkspaceContextError,
    accessible_workspaces,
    effective_workspace_role,
    require_capability,
    resolve_workspace_access,
)
from .workspace_serializers import (
    AuditEventSerializer,
    OrganizationMembershipSerializer,
    OrganizationSerializer,
    WorkspaceMembershipSerializer,
    WorkspaceSerializer,
)


def ok(data=None, message="success"):
    return Response({"code": 200, "message": message, "data": data})


def error(message, status_code=400, error_code=""):
    return Response(
        {"code": status_code, "message": message, "data": {"error_code": error_code} if error_code else None},
        status=status_code,
    )


def page_payload(request, queryset, serializer, *, context=None):
    try:
        size = max(1, min(int(request.query_params.get("page_size", 20)), 100))
    except (TypeError, ValueError):
        size = 20
    paginator = Paginator(queryset, size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return {
        "items": serializer(page.object_list, many=True, context=context or {}).data,
        "total": paginator.count,
        "page": page.number,
        "page_size": size,
        "total_pages": paginator.num_pages if paginator.count else 0,
    }


def _slug(name, prefix):
    return (slugify(name)[:80] or f"{prefix}-{uuid.uuid4().hex[:10]}").lower()


def _organization_for_user(user, organization_id):
    return Organization.objects.filter(pk=organization_id, memberships__user=user).first()


def _organization_role(user, organization):
    return organization.memberships.filter(user=user).values_list("role", flat=True).first()


def _organizations_for_user(user):
    current_role = OrganizationMembership.objects.filter(
        organization_id=OuterRef("pk"), user=user
    ).values("role")[:1]
    return Organization.objects.filter(memberships__user=user).annotate(
        resolved_current_user_role=Subquery(current_role),
        member_count=Count("memberships", distinct=True),
        workspace_count=Count("workspaces", distinct=True),
    ).distinct()


def _require_org_admin(request, organization, *, owner_only=False):
    role = _organization_role(request.user, organization)
    allowed = {OrganizationMembership.Role.OWNER}
    if not owner_only:
        allowed.add(OrganizationMembership.Role.ADMIN)
    if role not in allowed:
        record_audit_event(
            request,
            organization=organization,
            action="permission.denied",
            result=AuditEvent.Result.REJECTED,
            metadata={"capability": "organization.manage", "reason_code": "INSUFFICIENT_ROLE"},
        )
        raise WorkspaceContextError("当前角色无权执行此操作", 403, "PERMISSION_DENIED")
    return role


class GovernanceAPIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, WorkspaceContextError):
            return error(exc.message, exc.status_code, exc.error_code)
        return super().handle_exception(exc)


class MeContextView(GovernanceAPIView):
    def get(self, request):
        workspaces = list(accessible_workspaces(request.user).annotate(member_count=Count("memberships", distinct=True)))
        if not workspaces:
            resolve_workspace_access(request)
            workspaces = list(accessible_workspaces(request.user).annotate(member_count=Count("memberships", distinct=True)))
        organizations = _organizations_for_user(request.user)
        active = None
        header = str(request.headers.get("X-Workspace-ID", "")).strip()
        if header:
            try:
                active = next((item for item in workspaces if item.pk == int(header)), None)
            except ValueError:
                active = None
        if active is None and len(workspaces) == 1:
            active = workspaces[0]
        return ok({
            "organizations": OrganizationSerializer(organizations, many=True, context={"request": request}).data,
            "workspaces": WorkspaceSerializer(workspaces, many=True, context={"request": request}).data,
            "active_workspace_id": active.pk if active else None,
        })


class OrganizationListView(GovernanceAPIView):
    def get(self, request):
        queryset = _organizations_for_user(request.user)
        return ok(page_payload(request, queryset, OrganizationSerializer, context={"request": request}))

    def post(self, request):
        name = str(request.data.get("name", "")).strip()
        if not name or len(name) > 100:
            return error("组织名称必须为1到100个字符")
        with transaction.atomic():
            organization = Organization.objects.create(
                name=name, slug=f"{_slug(name, 'org')}-{uuid.uuid4().hex[:6]}", created_by=request.user
            )
            OrganizationMembership.objects.create(
                organization=organization, user=request.user,
                role=OrganizationMembership.Role.OWNER, created_by=request.user,
            )
            workspace = Workspace.objects.create(
                organization=organization, name="默认工作空间", slug="default",
                is_default=True, created_by=request.user,
            )
            WorkspaceMembership.objects.create(
                workspace=workspace, user=request.user,
                role=WorkspaceMembership.Role.ADMIN, created_by=request.user,
            )
        record_audit_event(request, organization=organization, workspace=workspace,
                           action="organization.create", resource_type="Organization", resource_id=organization.pk)
        return ok(OrganizationSerializer(organization, context={"request": request}).data, "组织创建成功")


class OrganizationDetailView(GovernanceAPIView):
    def get_object(self, request, organization_id):
        organization = _organization_for_user(request.user, organization_id)
        if not organization:
            raise WorkspaceContextError("组织不存在", 404, "ORGANIZATION_NOT_FOUND")
        return organization

    def get(self, request, organization_id):
        return ok(OrganizationSerializer(self.get_object(request, organization_id), context={"request": request}).data)

    def patch(self, request, organization_id):
        organization = self.get_object(request, organization_id)
        _require_org_admin(request, organization)
        fields = []
        if "name" in request.data:
            name = str(request.data["name"]).strip()
            if not name or len(name) > 100:
                return error("组织名称必须为1到100个字符")
            organization.name = name
            fields.append("name")
        if "status" in request.data:
            status_value = str(request.data["status"]).upper()
            if status_value not in Organization.Status.values:
                return error("组织状态无效")
            organization.status = status_value
            fields.append("status")
        if fields:
            fields.append("updated_at")
            organization.save(update_fields=fields)
        record_audit_event(request, organization=organization, action="organization.update",
                           resource_type="Organization", resource_id=organization.pk,
                           metadata={"changed_fields": fields})
        return ok(OrganizationSerializer(organization, context={"request": request}).data, "组织已更新")


class OrganizationMemberListView(GovernanceAPIView):
    def get_org(self, request, organization_id):
        organization = _organization_for_user(request.user, organization_id)
        if not organization:
            raise WorkspaceContextError("组织不存在", 404, "ORGANIZATION_NOT_FOUND")
        return organization

    def get(self, request, organization_id):
        organization = self.get_org(request, organization_id)
        return ok(page_payload(request, organization.memberships.select_related("user"), OrganizationMembershipSerializer))

    def post(self, request, organization_id):
        organization = self.get_org(request, organization_id)
        _require_org_admin(request, organization)
        username = str(request.data.get("username", "")).strip()
        role = str(request.data.get("role", OrganizationMembership.Role.MEMBER)).upper()
        if role not in OrganizationMembership.Role.values:
            return error("组织角色无效")
        if role == OrganizationMembership.Role.OWNER and _organization_role(request.user, organization) != OrganizationMembership.Role.OWNER:
            raise WorkspaceContextError("只有组织所有者可以添加所有者", 403, "PERMISSION_DENIED")
        user = User.objects.filter(username=username).first()
        if not user:
            return error("用户不存在")
        membership, created = OrganizationMembership.objects.get_or_create(
            organization=organization, user=user,
            defaults={"role": role, "created_by": request.user},
        )
        if not created:
            return error("该用户已经是组织成员", 409)
        record_audit_event(request, organization=organization, action="organization.member.add",
                           resource_type="OrganizationMembership", resource_id=membership.pk,
                           metadata={"target_user_id": user.pk, "member_role": role})
        return ok(OrganizationMembershipSerializer(membership).data, "组织成员已添加")


class OrganizationMemberDetailView(OrganizationMemberListView):
    def get_membership(self, request, organization_id, membership_id):
        organization = self.get_org(request, organization_id)
        membership = organization.memberships.select_related("user").filter(pk=membership_id).first()
        if not membership:
            raise WorkspaceContextError("组织成员不存在", 404, "MEMBERSHIP_NOT_FOUND")
        return organization, membership

    def patch(self, request, organization_id, membership_id):
        organization, membership = self.get_membership(request, organization_id, membership_id)
        actor_role = _require_org_admin(request, organization)
        role = str(request.data.get("role", "")).upper()
        if role not in OrganizationMembership.Role.values:
            return error("组织角色无效")
        if OrganizationMembership.Role.OWNER in {membership.role, role} and actor_role != OrganizationMembership.Role.OWNER:
            raise WorkspaceContextError("只有组织所有者可以调整所有者角色", 403, "PERMISSION_DENIED")
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization.pk)
            locked = OrganizationMembership.objects.select_for_update().get(pk=membership.pk)
            if locked.role == OrganizationMembership.Role.OWNER and role != locked.role:
                if organization.memberships.filter(role=OrganizationMembership.Role.OWNER).count() <= 1:
                    return error("组织必须至少保留一名所有者", 409, "LAST_OWNER")
            locked.role = role
            locked.save(update_fields=["role", "updated_at"])
        record_audit_event(request, organization=organization, action="organization.member.role.update",
                           resource_type="OrganizationMembership", resource_id=membership.pk,
                           metadata={"target_user_id": membership.user_id, "member_role": role})
        return ok(OrganizationMembershipSerializer(locked).data, "组织角色已更新")

    def delete(self, request, organization_id, membership_id):
        organization, membership = self.get_membership(request, organization_id, membership_id)
        actor_role = _require_org_admin(request, organization)
        if membership.role == OrganizationMembership.Role.OWNER and actor_role != OrganizationMembership.Role.OWNER:
            raise WorkspaceContextError("只有组织所有者可以移除所有者", 403, "PERMISSION_DENIED")
        target_user_id = membership.user_id
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization.pk)
            locked = OrganizationMembership.objects.select_for_update().get(pk=membership.pk)
            if locked.role == OrganizationMembership.Role.OWNER:
                if organization.memberships.filter(role=OrganizationMembership.Role.OWNER).count() <= 1:
                    return error("组织必须至少保留一名所有者", 409, "LAST_OWNER")
            WorkspaceMembership.objects.filter(
                workspace__organization=organization, user_id=target_user_id
            ).delete()
            locked.delete()
        record_audit_event(request, organization=organization, action="organization.member.remove",
                           resource_type="OrganizationMembership", resource_id=membership_id,
                           metadata={"target_user_id": target_user_id})
        return ok(True, "组织成员已移除")


class OrganizationWorkspaceListView(GovernanceAPIView):
    def get_org(self, request, organization_id):
        organization = _organization_for_user(request.user, organization_id)
        if not organization:
            raise WorkspaceContextError("组织不存在", 404, "ORGANIZATION_NOT_FOUND")
        return organization

    def get(self, request, organization_id):
        organization = self.get_org(request, organization_id)
        queryset = accessible_workspaces(request.user).filter(organization=organization).annotate(
            member_count=Count("memberships", distinct=True)
        )
        return ok(page_payload(request, queryset, WorkspaceSerializer, context={"request": request}))

    def post(self, request, organization_id):
        organization = self.get_org(request, organization_id)
        _require_org_admin(request, organization)
        name = str(request.data.get("name", "")).strip()
        if not name or len(name) > 100:
            return error("工作空间名称必须为1到100个字符")
        base_slug = _slug(name, "workspace")
        slug = base_slug
        index = 2
        while Workspace.objects.filter(organization=organization, slug=slug).exists():
            slug, index = f"{base_slug[:100]}-{index}", index + 1
        workspace = Workspace.objects.create(
            organization=organization, name=name, slug=slug, created_by=request.user
        )
        WorkspaceMembership.objects.get_or_create(
            workspace=workspace, user=request.user,
            defaults={"role": WorkspaceMembership.Role.ADMIN, "created_by": request.user},
        )
        record_audit_event(request, organization=organization, workspace=workspace,
                           action="workspace.create", resource_type="Workspace", resource_id=workspace.pk)
        return ok(WorkspaceSerializer(workspace, context={"request": request}).data, "工作空间创建成功")


class OrganizationWorkspaceDetailView(OrganizationWorkspaceListView):
    def get_workspace(self, request, organization_id, workspace_id):
        organization = self.get_org(request, organization_id)
        workspace = organization.workspaces.filter(pk=workspace_id).first()
        if not workspace or not effective_workspace_role(request.user, workspace):
            raise WorkspaceContextError("工作空间不存在", 404, "WORKSPACE_NOT_FOUND")
        return organization, workspace

    def get(self, request, organization_id, workspace_id):
        _, workspace = self.get_workspace(request, organization_id, workspace_id)
        return ok(WorkspaceSerializer(workspace, context={"request": request}).data)

    def patch(self, request, organization_id, workspace_id):
        organization, workspace = self.get_workspace(request, organization_id, workspace_id)
        _require_org_admin(request, organization)
        fields = []
        if "name" in request.data:
            name = str(request.data["name"]).strip()
            if not name or len(name) > 100:
                return error("工作空间名称必须为1到100个字符")
            workspace.name = name
            fields.append("name")
        if "status" in request.data:
            status_value = str(request.data["status"]).upper()
            if status_value not in Workspace.Status.values:
                return error("工作空间状态无效")
            workspace.status = status_value
            fields.append("status")
        if fields:
            fields.append("updated_at")
            workspace.save(update_fields=fields)
        record_audit_event(request, organization=organization, workspace=workspace,
                           action="workspace.update", resource_type="Workspace", resource_id=workspace.pk,
                           metadata={"changed_fields": fields})
        return ok(WorkspaceSerializer(workspace, context={"request": request}).data, "工作空间已更新")

    def delete(self, request, organization_id, workspace_id):
        organization, workspace = self.get_workspace(request, organization_id, workspace_id)
        _require_org_admin(request, organization)
        if workspace.is_default:
            return error("默认工作空间不能删除", 409, "DEFAULT_WORKSPACE")
        if KnowledgeBase.objects.filter(workspace=workspace).exists() or ModelConfig.objects.filter(workspace=workspace).exists() or Application.objects.filter(workspace=workspace).exists():
            return error("工作空间仍包含业务资源，不能删除", 409, "WORKSPACE_NOT_EMPTY")
        resource_id = workspace.pk
        workspace.delete()
        record_audit_event(request, organization=organization, action="workspace.delete",
                           resource_type="Workspace", resource_id=resource_id)
        return ok(True, "工作空间已删除")


class WorkspaceMemberListView(GovernanceAPIView):
    def get_workspace(self, request, workspace_id):
        workspace = accessible_workspaces(request.user).filter(pk=workspace_id).first()
        if not workspace:
            raise WorkspaceContextError("工作空间不存在", 404, "WORKSPACE_NOT_FOUND")
        return workspace

    def get(self, request, workspace_id):
        workspace = self.get_workspace(request, workspace_id)
        role = effective_workspace_role(request.user, workspace)
        if "member.manage" not in CAPABILITIES_BY_ROLE.get(role, set()) and role != WorkspaceMembership.Role.AUDITOR:
            raise WorkspaceContextError("当前角色无权查看成员", 403, "PERMISSION_DENIED")
        return ok(page_payload(request, workspace.memberships.select_related("user"), WorkspaceMembershipSerializer))

    def post(self, request, workspace_id):
        workspace = self.get_workspace(request, workspace_id)
        access = require_capability(request, "member.manage", workspace)
        username = str(request.data.get("username", "")).strip()
        role = str(request.data.get("role", WorkspaceMembership.Role.VIEWER)).upper()
        if role not in WorkspaceMembership.Role.values:
            return error("工作空间角色无效")
        user = User.objects.filter(username=username, organization_memberships__organization=workspace.organization).first()
        if not user:
            return error("用户必须先加入所属组织")
        membership, created = WorkspaceMembership.objects.get_or_create(
            workspace=workspace, user=user,
            defaults={"role": role, "created_by": request.user},
        )
        if not created:
            return error("该用户已经是工作空间成员", 409)
        record_audit_event(request, organization=workspace.organization, workspace=access.workspace,
                           action="workspace.member.add", resource_type="WorkspaceMembership", resource_id=membership.pk,
                           metadata={"target_user_id": user.pk, "member_role": role})
        return ok(WorkspaceMembershipSerializer(membership).data, "工作空间成员已添加")


class WorkspaceMemberDetailView(WorkspaceMemberListView):
    def get_membership(self, request, workspace_id, membership_id):
        workspace = self.get_workspace(request, workspace_id)
        require_capability(request, "member.manage", workspace)
        membership = workspace.memberships.select_related("user").filter(pk=membership_id).first()
        if not membership:
            raise WorkspaceContextError("工作空间成员不存在", 404, "MEMBERSHIP_NOT_FOUND")
        return workspace, membership

    def patch(self, request, workspace_id, membership_id):
        workspace, membership = self.get_membership(request, workspace_id, membership_id)
        role = str(request.data.get("role", "")).upper()
        if role not in WorkspaceMembership.Role.values:
            return error("工作空间角色无效")
        membership.role = role
        membership.save(update_fields=["role", "updated_at"])
        record_audit_event(request, organization=workspace.organization, workspace=workspace,
                           action="workspace.member.role.update", resource_type="WorkspaceMembership",
                           resource_id=membership.pk, metadata={"target_user_id": membership.user_id, "member_role": role})
        return ok(WorkspaceMembershipSerializer(membership).data, "工作空间角色已更新")

    def delete(self, request, workspace_id, membership_id):
        workspace, membership = self.get_membership(request, workspace_id, membership_id)
        target_user_id = membership.user_id
        membership.delete()
        record_audit_event(request, organization=workspace.organization, workspace=workspace,
                           action="workspace.member.remove", resource_type="WorkspaceMembership",
                           resource_id=membership_id, metadata={"target_user_id": target_user_id})
        return ok(True, "工作空间成员已移除")


class AuditEventListView(GovernanceAPIView):
    def get(self, request):
        access = require_capability(request, "audit.read")
        queryset = AuditEvent.objects.filter(organization=access.workspace.organization).select_related("actor", "workspace")
        if request.query_params.get("all_workspaces") != "true":
            queryset = queryset.filter(Q(workspace=access.workspace) | Q(workspace__isnull=True))
        action = request.query_params.get("action", "").strip()
        result = request.query_params.get("result", "").strip().upper()
        actor_id = request.query_params.get("actor_id", "").strip()
        if action:
            queryset = queryset.filter(action__icontains=action)
        if result in AuditEvent.Result.values:
            queryset = queryset.filter(result=result)
        if actor_id.isdigit():
            queryset = queryset.filter(actor_id=int(actor_id))
        if request.query_params.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=request.query_params["date_from"])
        if request.query_params.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=request.query_params["date_to"])
        return ok(page_payload(request, queryset, AuditEventSerializer))
