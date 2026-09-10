from rest_framework import serializers

from .models import (
    AuditEvent,
    Organization,
    OrganizationMembership,
    Workspace,
    WorkspaceMembership,
)
from .services.workspace_permissions import CAPABILITIES_BY_ROLE, effective_workspace_role


class OrganizationSerializer(serializers.ModelSerializer):
    current_user_role = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True, default=0)
    workspace_count = serializers.IntegerField(read_only=True, default=0)

    def get_current_user_role(self, obj):
        resolved = getattr(obj, "resolved_current_user_role", None)
        if resolved is not None:
            return resolved
        request = self.context.get("request")
        if not request:
            return None
        return obj.memberships.filter(user=request.user).values_list("role", flat=True).first()

    class Meta:
        model = Organization
        fields = [
            "id", "name", "slug", "status", "current_user_role", "member_count",
            "workspace_count", "created_at", "updated_at",
        ]
        read_only_fields = ["slug", "created_at", "updated_at"]


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ["id", "user_id", "username", "role", "created_at", "updated_at"]


class WorkspaceSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    current_user_role = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True, default=0)

    def get_current_user_role(self, obj):
        request = self.context.get("request")
        return effective_workspace_role(request.user, obj) if request else None

    def get_capabilities(self, obj):
        role = self.get_current_user_role(obj)
        return sorted(CAPABILITIES_BY_ROLE.get(role, set()))

    class Meta:
        model = Workspace
        fields = [
            "id", "organization_id", "organization_name", "name", "slug", "status",
            "is_default", "current_user_role", "capabilities", "member_count",
            "created_at", "updated_at",
        ]
        read_only_fields = ["slug", "is_default", "created_at", "updated_at"]


class WorkspaceMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = WorkspaceMembership
        fields = ["id", "user_id", "username", "role", "created_at", "updated_at"]


class AuditEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True, allow_null=True)
    workspace_name = serializers.CharField(source="workspace.name", read_only=True, allow_null=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id", "organization_id", "workspace_id", "workspace_name", "actor_id",
            "actor_username", "action", "resource_type", "resource_id", "result",
            "request_id", "ip_hash", "metadata", "created_at",
        ]
        read_only_fields = fields
