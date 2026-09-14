import re
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import (
    AccountProfile, KnowledgeBase, OrganizationInvitation, OrganizationMembership,
)
from .services.workspaces import ensure_personal_workspace


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    LOGIN_RATE_LIMIT_ATTEMPTS=3,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=60,
)
class Stage14AuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="stage14-owner", email="Owner@Example.com", password="Stage14-safe-pass-2026"
        )
        profile = AccountProfile.objects.get(user=self.user)
        profile.normalized_email = "owner@example.com"
        profile.save(update_fields=["normalized_email", "updated_at"])

    def csrf_client(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get("/api/auth/csrf/")
        self.assertEqual(response.status_code, 200)
        return client, response.cookies["csrftoken"].value

    def test_session_login_requires_csrf_and_recovers_user(self):
        client = Client(enforce_csrf_checks=True)
        denied = client.post("/api/auth/login/", {"username": self.user.username, "password": "Stage14-safe-pass-2026"})
        self.assertEqual(denied.status_code, 403)
        client, csrf = self.csrf_client()
        response = client.post("/api/auth/login/", {"username": self.user.username, "password": "Stage14-safe-pass-2026"},
                               HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertIn("sessionid", response.cookies)
        me = client.get("/api/auth/me/")
        self.assertEqual(me.json()["data"]["username"], self.user.username)
        self.assertNotIn("token", me.json()["data"])

    def test_legacy_token_authentication_remains_compatible(self):
        token = Token.objects.create(user=self.user)
        client = APIClient()
        response = client.get("/api/me/context/", HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(response.status_code, 200)

    def test_login_failure_rate_limit_has_safe_message(self):
        client, csrf = self.csrf_client()
        for _ in range(3):
            client.post("/api/auth/login/", {"username": "missing", "password": "wrong"}, HTTP_X_CSRFTOKEN=csrf)
        response = client.post("/api/auth/login/", {"username": "missing", "password": "wrong"}, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("missing", response.content.decode())

    def test_password_reset_does_not_enumerate_accounts(self):
        client, csrf = self.csrf_client()
        existing = client.post("/api/auth/password-reset/request/", {"email": "owner@example.com"}, HTTP_X_CSRFTOKEN=csrf)
        missing = client.post("/api/auth/password-reset/request/", {"email": "absent@example.com"}, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(existing.json()["message"], missing.json()["message"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("Stage14-safe-pass-2026", mail.outbox[0].body)

    def test_email_verification_uses_expiring_signed_link(self):
        client = Client(); client.force_login(self.user)
        response = client.post("/api/auth/email-verification/request/")
        self.assertEqual(response.status_code, 200)
        token = parse_qs(urlparse(mail.outbox[-1].body.splitlines()[-1]).query)["token"][0]
        confirmed = Client().post("/api/auth/email-verification/confirm/", data={"token": token}, content_type="application/json")
        self.assertEqual(confirmed.status_code, 200)
        self.assertIsNotNone(AccountProfile.objects.get(user=self.user).email_verified_at)

    def test_user_can_revoke_another_login_session(self):
        first, second = Client(), Client()
        first.force_login(self.user); second.force_login(self.user)
        first.get("/api/auth/me/"); second.get("/api/auth/me/")
        sessions = first.get("/api/auth/sessions/").json()["data"]["items"]
        other = next(item for item in sessions if not item["current"])
        self.assertEqual(first.delete(f"/api/auth/sessions/{other['id']}/").status_code, 200)
        self.assertEqual(second.get("/api/auth/me/").status_code, 401)


class Stage14InvitationAndDashboardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="stage14-admin", email="admin@example.com", password="Stage14-admin-pass-2026")
        self.workspace = ensure_personal_workspace(self.owner)
        profile = AccountProfile.objects.get(user=self.owner)
        profile.normalized_email = "admin@example.com"
        profile.save(update_fields=["normalized_email", "updated_at"])
        self.organization = self.workspace.organization
        self.client = Client()
        self.client.force_login(self.owner)

    def create_invitation(self, email="member@example.com"):
        response = self.client.post(
            f"/api/organizations/{self.organization.id}/invitations/",
            data={"email": email, "organization_role": "MEMBER",
                  "workspace_grants": [{"workspace_id": self.workspace.id, "role": "VIEWER"}]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()["data"]["invitation_url"]
        return response.json()["data"], url.rsplit("/", 1)[-1]

    def test_invitation_stores_digest_and_masks_preview(self):
        data, token = self.create_invitation()
        invitation = OrganizationInvitation.objects.get(pk=data["id"])
        self.assertNotEqual(invitation.token_digest, token)
        self.assertNotIn(token, str(invitation.__dict__))
        preview = Client().get(f"/api/invitations/{token}/preview/")
        self.assertEqual(preview.status_code, 200)
        body = preview.json()["data"]
        self.assertEqual(body["email_masked"], "me***@example.com")
        self.assertNotIn("member@example.com", preview.content.decode())

    def test_invitation_cannot_grant_owner(self):
        response = self.client.post(
            f"/api/organizations/{self.organization.id}/invitations/",
            data={"email": "member@example.com", "organization_role": "OWNER", "workspace_grants": []},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_matching_user_accepts_once_and_gets_memberships(self):
        _, token = self.create_invitation()
        member = User.objects.create_user(username="stage14-member", email="member@example.com", password="Stage14-member-pass-2026")
        profile = AccountProfile.objects.get(user=member)
        profile.normalized_email = "member@example.com"
        profile.save(update_fields=["normalized_email", "updated_at"])
        client = Client(); client.force_login(member)
        accepted = client.post(f"/api/invitations/{token}/accept/")
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertTrue(OrganizationMembership.objects.filter(organization=self.organization, user=member).exists())
        self.assertTrue(self.workspace.memberships.filter(user=member).exists())
        replay = client.post(f"/api/invitations/{token}/accept/")
        self.assertEqual(replay.status_code, 410)

    def test_dashboard_is_scoped_to_selected_workspace(self):
        KnowledgeBase.objects.create(name="stage14-visible", owner=self.owner, workspace=self.workspace)
        other = User.objects.create_user(username="stage14-other", password="Stage14-other-pass-2026")
        other_workspace = ensure_personal_workspace(other)
        KnowledgeBase.objects.create(name="stage14-hidden", owner=other, workspace=other_workspace)
        response = self.client.get("/api/dashboard/overview/", HTTP_X_WORKSPACE_ID=str(self.workspace.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["counts"]["knowledge_bases"], 1)

    def test_non_manager_cannot_create_invitation(self):
        member = User.objects.create_user(username="stage14-basic", password="Stage14-basic-pass-2026")
        OrganizationMembership.objects.create(organization=self.organization, user=member, role="MEMBER")
        client = Client(); client.force_login(member)
        response = client.post(f"/api/organizations/{self.organization.id}/invitations/",
                               data={"email": "x@example.com", "organization_role": "MEMBER", "workspace_grants": []},
                               content_type="application/json")
        self.assertEqual(response.status_code, 403)
