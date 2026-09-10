from dataclasses import dataclass

from django.db.models import OuterRef, Q, Subquery

from api.models import OrganizationMembership, Workspace, WorkspaceMembership

from .workspaces import ensure_personal_workspace


CAPABILITIES_BY_ROLE = {
    WorkspaceMembership.Role.ADMIN: {
        "workspace.read", "knowledge.read", "knowledge.chat", "knowledge.write",
        "document.process", "model.read", "model.manage", "application.read",
        "application.write", "application.operate", "member.manage", "audit.read",
    },
    WorkspaceMembership.Role.DEVELOPER: {
        "workspace.read", "knowledge.read", "knowledge.chat", "knowledge.write",
        "document.process", "model.read", "model.manage", "application.read",
        "application.write", "application.operate",
    },
    WorkspaceMembership.Role.OPERATOR: {
        "workspace.read", "knowledge.read", "knowledge.chat", "document.process",
        "model.read", "application.read", "application.operate",
    },
    WorkspaceMembership.Role.VIEWER: {
        "workspace.read", "knowledge.read", "knowledge.chat", "model.read", "application.read",
    },
    WorkspaceMembership.Role.AUDITOR: {
        "workspace.read", "knowledge.read", "model.read", "application.read", "audit.read",
    },
}


class WorkspaceContextError(Exception):
    def __init__(self, message, status_code=400, error_code="WORKSPACE_CONTEXT_REQUIRED"):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace: Workspace
    role: str
    capabilities: frozenset[str]


def accessible_workspaces(user):
    organization_role = OrganizationMembership.objects.filter(
        organization_id=OuterRef("organization_id"), user=user
    ).values("role")[:1]
    workspace_role = WorkspaceMembership.objects.filter(
        workspace_id=OuterRef("pk"), user=user
    ).values("role")[:1]
    return (
        Workspace.objects.filter(
            status=Workspace.Status.ACTIVE,
            organization__status="ACTIVE",
        )
        .filter(
            Q(memberships__user=user)
            | Q(organization__memberships__user=user, organization__memberships__role__in=[
                OrganizationMembership.Role.OWNER,
                OrganizationMembership.Role.ADMIN,
                OrganizationMembership.Role.AUDITOR,
            ])
        )
        .select_related("organization")
        .annotate(
            resolved_organization_role=Subquery(organization_role),
            resolved_workspace_role=Subquery(workspace_role),
        )
        .distinct()
    )


def effective_workspace_role(user, workspace) -> str | None:
    organization_role = getattr(workspace, "resolved_organization_role", None)
    workspace_role = getattr(workspace, "resolved_workspace_role", None)
    if organization_role is None and workspace_role is None:
        organization_role = OrganizationMembership.objects.filter(
            organization=workspace.organization, user=user
        ).values_list("role", flat=True).first()
    if organization_role in {OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN}:
        return WorkspaceMembership.Role.ADMIN
    if organization_role == OrganizationMembership.Role.AUDITOR:
        return WorkspaceMembership.Role.AUDITOR
    if workspace_role is not None:
        return workspace_role
    return WorkspaceMembership.objects.filter(workspace=workspace, user=user).values_list("role", flat=True).first()


def resolve_workspace_access(request) -> WorkspaceAccess:
    cached = getattr(request, "workspace_access", None)
    if cached is not None:
        return cached
    header = str(request.headers.get("X-Workspace-ID", "")).strip()
    queryset = accessible_workspaces(request.user)
    if header:
        try:
            workspace_id = int(header)
        except (TypeError, ValueError):
            raise WorkspaceContextError("工作空间不存在", 404, "WORKSPACE_NOT_FOUND")
        workspace = queryset.filter(pk=workspace_id).first()
        if not workspace:
            raise WorkspaceContextError("工作空间不存在", 404, "WORKSPACE_NOT_FOUND")
    else:
        candidates = list(queryset[:2])
        if not candidates:
            ensure_personal_workspace(request.user)
            candidates = list(accessible_workspaces(request.user)[:2])
        if len(candidates) != 1:
            raise WorkspaceContextError("请选择工作空间后重试", 400, "WORKSPACE_CONTEXT_REQUIRED")
        workspace = candidates[0]
    role = effective_workspace_role(request.user, workspace)
    if not role:
        raise WorkspaceContextError("工作空间不存在", 404, "WORKSPACE_NOT_FOUND")
    access = WorkspaceAccess(workspace, role, frozenset(CAPABILITIES_BY_ROLE.get(role, set())))
    request.workspace_access = access
    return access


def require_capability(request, capability: str, workspace=None) -> WorkspaceAccess:
    access = getattr(request, "workspace_access", None)
    if access is None or (workspace is not None and access.workspace.pk != workspace.pk):
        access = resolve_workspace_access(request)
        request.workspace_access = access
    if workspace is not None and access.workspace.pk != workspace.pk:
        raise WorkspaceContextError("资源不存在", 404, "RESOURCE_NOT_FOUND")
    if capability not in access.capabilities:
        from .audit import record_audit_event

        record_audit_event(
            request,
            organization=access.workspace.organization,
            workspace=access.workspace,
            action="permission.denied",
            result="REJECTED",
            metadata={"capability": capability, "reason_code": "INSUFFICIENT_CAPABILITY"},
        )
        raise WorkspaceContextError("当前角色无权执行此操作", 403, "PERMISSION_DENIED")
    return access
