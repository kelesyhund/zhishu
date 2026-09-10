from django.db import transaction

from api.models import (
    Organization,
    OrganizationMembership,
    Workspace,
    WorkspaceMembership,
)


@transaction.atomic
def ensure_personal_workspace(user) -> Workspace:
    """为新用户或历史遗漏用户创建确定性的个人租户上下文。"""

    existing = (
        Workspace.objects.filter(
            organization__memberships__user=user,
            is_default=True,
        )
        .order_by("id")
        .first()
    )
    if existing:
        return existing

    organization, _ = Organization.objects.get_or_create(
        slug=f"personal-u{user.pk}",
        defaults={
            "name": f"{user.username} 的个人组织"[:100],
            "created_by": user,
        },
    )
    OrganizationMembership.objects.get_or_create(
        organization=organization,
        user=user,
        defaults={"role": OrganizationMembership.Role.OWNER, "created_by": user},
    )
    workspace, _ = Workspace.objects.get_or_create(
        organization=organization,
        slug="default",
        defaults={"name": "默认工作空间", "is_default": True, "created_by": user},
    )
    WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.ADMIN, "created_by": user},
    )
    return workspace

