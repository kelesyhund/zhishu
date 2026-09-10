from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import OrganizationMembership, WorkspaceMembership


class RegistrationTests(APITestCase):
    def registration_payload(self, **overrides):
        payload = {
            "username": "new-user",
            "email": "New.User@example.com",
            "password": "secure-pass-123",
            "password_confirm": "secure-pass-123",
        }
        payload.update(overrides)
        return payload

    def test_registration_creates_user_token_and_personal_workspace(self):
        response = self.client.post("/api/register/", self.registration_payload(), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["message"], "注册成功")
        self.assertEqual(response.data["data"]["username"], "new-user")
        self.assertNotIn("password", response.data["data"])

        user = User.objects.get(username="new-user")
        self.assertEqual(user.email, "new.user@example.com")
        self.assertTrue(user.check_password("secure-pass-123"))
        self.assertEqual(Token.objects.get(user=user).key, response.data["data"]["token"])
        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=user, role=OrganizationMembership.Role.OWNER
            ).exists()
        )
        self.assertTrue(
            WorkspaceMembership.objects.filter(
                user=user, role=WorkspaceMembership.Role.ADMIN, workspace__is_default=True
            ).exists()
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['data']['token']}")
        context = self.client.get("/api/me/context/")
        self.assertEqual(context.status_code, 200)
        self.assertEqual(len(context.data["data"]["workspaces"]), 1)

    def test_registration_rejects_case_insensitive_duplicate_username(self):
        User.objects.create_user("ExistingUser", password="secure-pass-123")
        response = self.client.post(
            "/api/register/",
            self.registration_payload(username="existinguser"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["message"], "该用户名已被使用")
        self.assertEqual(User.objects.filter(username__iexact="existinguser").count(), 1)

    def test_registration_validates_confirmation_and_password_strength(self):
        mismatch = self.client.post(
            "/api/register/",
            self.registration_payload(password_confirm="different-pass"),
            format="json",
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("两次输入的密码不一致", mismatch.data["message"])

        too_short = self.client.post(
            "/api/register/",
            self.registration_payload(password="short", password_confirm="short"),
            format="json",
        )
        self.assertEqual(too_short.status_code, 400)
        self.assertFalse(User.objects.filter(username="new-user").exists())

    @override_settings(ALLOW_USER_REGISTRATION=False)
    def test_registration_can_be_disabled(self):
        response = self.client.post("/api/register/", self.registration_payload(), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("未开放用户注册", response.data["message"])
        self.assertFalse(User.objects.filter(username="new-user").exists())
