from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from .models import (
    Application,
    AuditEvent,
    KnowledgeBase,
    ModelConfig,
    OrganizationMembership,
    Workspace,
    WorkspaceMembership,
)
from .services.audit import safe_metadata
from .services.workspace_permissions import CAPABILITIES_BY_ROLE


class StageThirteenWorkspaceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("stage13-owner", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.default_workspace = Workspace.objects.get(
            organization__memberships__user=self.owner,
            is_default=True,
        )
        self.organization = self.default_workspace.organization

    def headers(self, workspace=None):
        return {"HTTP_X_WORKSPACE_ID": str((workspace or self.default_workspace).id)}

    def test_new_user_receives_personal_tenant_and_legacy_create_is_backfilled(self):
        membership = OrganizationMembership.objects.get(
            organization=self.organization, user=self.owner
        )
        workspace_membership = WorkspaceMembership.objects.get(
            workspace=self.default_workspace, user=self.owner
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.OWNER)
        self.assertEqual(workspace_membership.role, WorkspaceMembership.Role.ADMIN)
        knowledge = KnowledgeBase.objects.create(owner=self.owner, name="legacy knowledge")
        self.assertEqual(knowledge.workspace_id, self.default_workspace.id)

    def test_multiple_workspaces_require_header_and_context_lists_both(self):
        second = Workspace.objects.create(
            organization=self.organization,
            name="研发空间",
            slug="engineering",
            created_by=self.owner,
        )
        response = self.client.get("/api/knowledge-bases/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["data"]["error_code"], "WORKSPACE_CONTEXT_REQUIRED")
        context = self.client.get("/api/me/context/")
        self.assertEqual(context.status_code, 200)
        self.assertEqual({item["id"] for item in context.data["data"]["workspaces"]}, {
            self.default_workspace.id, second.id,
        })

    def test_cross_workspace_root_resources_are_hidden(self):
        second = Workspace.objects.create(
            organization=self.organization, name="隔离空间", slug="isolated", created_by=self.owner
        )
        knowledge = KnowledgeBase.objects.create(
            owner=self.owner, workspace=second, name="other workspace knowledge"
        )
        application = Application.objects.create(
            owner=self.owner, workspace=second, name="other workspace application"
        )
        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/", **self.headers(self.default_workspace)
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.get(
            f"/api/applications/{application.id}/", **self.headers(self.default_workspace)
        )
        self.assertEqual(response.status_code, 404)

    def test_header_for_inaccessible_workspace_returns_404(self):
        stranger = User.objects.create_user("stage13-stranger", password="pass")
        stranger_workspace = Workspace.objects.get(
            organization__memberships__user=stranger, is_default=True
        )
        response = self.client.get(
            "/api/knowledge-bases/", **self.headers(stranger_workspace)
        )
        self.assertEqual(response.status_code, 404)

    def test_workspace_roles_enforce_read_write_and_audit_capabilities(self):
        member = User.objects.create_user("stage13-member", password="pass")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=member,
            role=OrganizationMembership.Role.MEMBER,
            created_by=self.owner,
        )
        workspace_membership = WorkspaceMembership.objects.create(
            workspace=self.default_workspace,
            user=member,
            role=WorkspaceMembership.Role.VIEWER,
            created_by=self.owner,
        )
        member_client = APIClient()
        member_client.force_authenticate(member)
        headers = self.headers()

        self.assertEqual(member_client.get("/api/knowledge-bases/", **headers).status_code, 200)
        self.assertEqual(
            member_client.post("/api/knowledge-bases/", {"name": "forbidden"}, format="json", **headers).status_code,
            403,
        )
        workspace_membership.role = WorkspaceMembership.Role.DEVELOPER
        workspace_membership.save(update_fields=["role"])
        self.assertEqual(
            member_client.post("/api/knowledge-bases/", {"name": "allowed"}, format="json", **headers).status_code,
            200,
        )
        workspace_membership.role = WorkspaceMembership.Role.AUDITOR
        workspace_membership.save(update_fields=["role"])
        self.assertEqual(member_client.get("/api/audit-events/", **headers).status_code, 200)
        knowledge = KnowledgeBase.objects.filter(workspace=self.default_workspace).first()
        self.assertEqual(
            member_client.post(
                f"/api/knowledge-bases/{knowledge.id}/chat/stream/",
                {"message": "test"}, format="json", **headers,
            ).status_code,
            403,
        )

    def test_capability_matrix_has_no_write_privileges_for_viewer_or_auditor(self):
        for role in (WorkspaceMembership.Role.VIEWER, WorkspaceMembership.Role.AUDITOR):
            capabilities = CAPABILITIES_BY_ROLE[role]
            self.assertNotIn("knowledge.write", capabilities)
            self.assertNotIn("model.manage", capabilities)
            self.assertNotIn("application.write", capabilities)
        self.assertIn("document.process", CAPABILITIES_BY_ROLE[WorkspaceMembership.Role.OPERATOR])
        self.assertNotIn("knowledge.write", CAPABILITIES_BY_ROLE[WorkspaceMembership.Role.OPERATOR])

    def test_last_owner_cannot_be_demoted_or_removed(self):
        membership = OrganizationMembership.objects.get(
            organization=self.organization, user=self.owner
        )
        response = self.client.patch(
            f"/api/organizations/{self.organization.id}/members/{membership.id}/",
            {"role": "ADMIN"}, format="json",
        )
        self.assertEqual(response.status_code, 409)
        response = self.client.delete(
            f"/api/organizations/{self.organization.id}/members/{membership.id}/"
        )
        self.assertEqual(response.status_code, 409)

    def test_workspace_model_and_knowledge_binding_cannot_cross_boundary(self):
        second = Workspace.objects.create(
            organization=self.organization, name="模型隔离", slug="model-isolation", created_by=self.owner
        )
        config = ModelConfig.objects.create(
            owner=self.owner,
            workspace=second,
            name="external",
            model_type=ModelConfig.ModelType.CHAT,
            base_url="https://example.com/v1",
            model_name="test",
            encrypted_api_key="not-a-real-key",
        )
        knowledge = KnowledgeBase.objects.create(
            owner=self.owner, workspace=self.default_workspace, name="local"
        )
        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/model-config/",
            {"chat_model_config_id": config.id}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_audit_is_read_only_and_metadata_drops_secrets(self):
        payload = safe_metadata({
            "capability": "model.manage",
            "api_key": "stage13-secret",
            "token": "stage13-token",
            "content": "private document",
            "status": "updated",
        })
        self.assertEqual(payload, {"capability": "model.manage", "status": "updated"})
        response = self.client.post(
            "/api/audit-events/", {"action": "forged"}, format="json", **self.headers()
        )
        self.assertEqual(response.status_code, 405)
        self.assertFalse(AuditEvent.objects.filter(action="forged").exists())

    def test_permission_denial_creates_rejected_audit_without_sensitive_data(self):
        member = User.objects.create_user("stage13-viewer", password="pass")
        OrganizationMembership.objects.create(
            organization=self.organization, user=member,
            role=OrganizationMembership.Role.MEMBER, created_by=self.owner,
        )
        WorkspaceMembership.objects.create(
            workspace=self.default_workspace, user=member,
            role=WorkspaceMembership.Role.VIEWER, created_by=self.owner,
        )
        client = APIClient()
        client.force_authenticate(member)
        response = client.post(
            "/api/model-configs/", {"api_key": "stage13-should-never-be-audited"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)
        event = AuditEvent.objects.filter(action="permission.denied").latest("id")
        self.assertEqual(event.result, AuditEvent.Result.REJECTED)
        self.assertNotIn("stage13-should-never-be-audited", str(event.metadata))

    def test_public_health_and_login_do_not_require_workspace_header(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/health/").status_code, 200)
        self.assertNotEqual(
            anonymous.post("/api/login/", {"username": "missing", "password": "bad"}).status_code,
            400,
        )


class StageThirteenMigrationTests(TransactionTestCase):
    migrate_from = ("api", "0009_alter_document_source_id")
    migrate_to = ("api", "0012_enforce_workspace_ownership")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        User = old_apps.get_model("auth", "User")
        KnowledgeBase = old_apps.get_model("api", "KnowledgeBase")
        ModelConfig = old_apps.get_model("api", "ModelConfig")
        Application = old_apps.get_model("api", "Application")
        user = User.objects.create(username="stage13-migration-user")
        self.user_id = user.pk
        self.knowledge_id = KnowledgeBase.objects.create(owner_id=user.pk, name="legacy kb").pk
        self.config_id = ModelConfig.objects.create(
            owner_id=user.pk,
            name="legacy model",
            model_type="CHAT",
            base_url="https://example.com/v1",
            model_name="test",
            encrypted_api_key="encrypted",
        ).pk
        self.application_id = Application.objects.create(owner_id=user.pk, name="legacy app").pk
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_user_and_root_resources_are_backfilled_without_loss(self):
        OrganizationMembership = self.apps.get_model("api", "OrganizationMembership")
        WorkspaceMembership = self.apps.get_model("api", "WorkspaceMembership")
        KnowledgeBase = self.apps.get_model("api", "KnowledgeBase")
        ModelConfig = self.apps.get_model("api", "ModelConfig")
        Application = self.apps.get_model("api", "Application")
        organization_membership = OrganizationMembership.objects.get(user_id=self.user_id)
        workspace_membership = WorkspaceMembership.objects.get(user_id=self.user_id)
        self.assertEqual(organization_membership.role, "OWNER")
        self.assertEqual(workspace_membership.role, "ADMIN")
        workspace_id = workspace_membership.workspace_id
        self.assertEqual(KnowledgeBase.objects.get(pk=self.knowledge_id).workspace_id, workspace_id)
        self.assertEqual(ModelConfig.objects.get(pk=self.config_id).workspace_id, workspace_id)
        self.assertEqual(Application.objects.get(pk=self.application_id).workspace_id, workspace_id)
