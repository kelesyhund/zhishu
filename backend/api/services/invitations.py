import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.models import (
    InvitationWorkspaceGrant,
    OrganizationInvitation,
    OrganizationMembership,
    WorkspaceMembership,
)

from .account_security import ensure_profile, normalize_email


class InvitationError(Exception):
    def __init__(self, message: str, status_code: int = 400, error_code: str = "INVITATION_INVALID"):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}" if domain else "***"


def invitation_payload(invitation):
    return {
        "id": invitation.id,
        "email_masked": mask_email(invitation.email_normalized),
        "organization_role": invitation.organization_role,
        "status": invitation.status,
        "expires_at": invitation.expires_at,
        "workspace_grants": [
            {"workspace_id": grant.workspace_id, "workspace_name": grant.workspace.name,
             "role": grant.workspace_role}
            for grant in invitation.workspace_grants.select_related("workspace").all()
        ],
        "created_at": invitation.created_at,
    }


@transaction.atomic
def create_invitation(organization, invited_by, email: str, organization_role: str, grants: list[dict]):
    normalized = normalize_email(email)
    if organization_role == OrganizationMembership.Role.OWNER:
        raise InvitationError("邀请不能授予组织所有者角色")
    OrganizationInvitation.objects.select_for_update().filter(
        organization=organization, email_normalized=normalized,
        status=OrganizationInvitation.Status.PENDING,
    ).update(status=OrganizationInvitation.Status.REVOKED)
    raw_token = secrets.token_urlsafe(32)
    invitation = OrganizationInvitation.objects.create(
        organization=organization,
        email_normalized=normalized,
        organization_role=organization_role,
        token_digest=token_digest(raw_token),
        expires_at=timezone.now() + timedelta(hours=settings.INVITATION_EXPIRE_HOURS),
        invited_by=invited_by,
    )
    for item in grants:
        InvitationWorkspaceGrant.objects.create(
            invitation=invitation, workspace=item["workspace"], workspace_role=item["role"]
        )
    return invitation, raw_token


def resolve_invitation(raw_token: str, *, for_update=False):
    queryset = OrganizationInvitation.objects.select_related("organization")
    if for_update:
        queryset = queryset.select_for_update()
    invitation = queryset.filter(token_digest=token_digest(raw_token)).first()
    if not invitation:
        raise InvitationError("邀请链接无效或已失效", 404)
    if invitation.status == OrganizationInvitation.Status.PENDING and invitation.expires_at <= timezone.now():
        invitation.status = OrganizationInvitation.Status.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
    if invitation.status != OrganizationInvitation.Status.PENDING:
        raise InvitationError("邀请链接无效或已失效", 410, "INVITATION_NOT_PENDING")
    return invitation


@transaction.atomic
def accept_invitation(raw_token: str, user):
    invitation = resolve_invitation(raw_token, for_update=True)
    profile = ensure_profile(user)
    email = normalize_email(profile.normalized_email or user.email)
    if not email or not hmac.compare_digest(email, invitation.email_normalized):
        raise InvitationError("当前账号邮箱与邀请邮箱不匹配", 403, "INVITATION_EMAIL_MISMATCH")
    OrganizationMembership.objects.update_or_create(
        organization=invitation.organization, user=user,
        defaults={"role": invitation.organization_role, "created_by": invitation.invited_by},
    )
    for grant in invitation.workspace_grants.select_related("workspace"):
        WorkspaceMembership.objects.update_or_create(
            workspace=grant.workspace, user=user,
            defaults={"role": grant.workspace_role, "created_by": invitation.invited_by},
        )
    invitation.status = OrganizationInvitation.Status.ACCEPTED
    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_by", "accepted_at", "updated_at"])
    return invitation
