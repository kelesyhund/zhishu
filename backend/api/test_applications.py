import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from api.models import (
    Application,
    ApplicationAccessLog,
    ApplicationCredential,
    ApplicationKnowledgeBase,
    AgentRun,
    Conversation,
    Document,
    KnowledgeBase,
    Message,
    Paragraph,
)
from api.services.application_credentials import authenticate_application_credential
from api.services.application_public_access import authenticate_public_token
from api.services.application_rate_limit import (
    RateLimitExceeded,
    enforce_rate_limit,
    reset_memory_rate_limits,
)
from api.services.application_retrieval import retrieve_application_context
from api.services.application_runtime import resolve_draft_runtime
from api.logging_filters import ApplicationSecretFilter


@override_settings(APPLICATION_RATE_LIMIT_BACKEND="memory")
class ApplicationApiTests(TestCase):
    def setUp(self):
        reset_memory_rate_limits()
        self.user = User.objects.create_user("stage10_owner", password="password")
        self.other = User.objects.create_user("stage10_other", password="password")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.user).key}")
        self.other_client = APIClient()
        self.other_client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.other).key}"
        )
        self.knowledge = KnowledgeBase.objects.create(
            owner=self.user,
            name="stage10_knowledge",
            retrieval_top_k=5,
        )
        self.other_knowledge = KnowledgeBase.objects.create(
            owner=self.other,
            name="stage10_other_knowledge",
        )
        self.document = Document.objects.create(
            knowledge_base=self.knowledge,
            name="stage10_document.txt",
            file="documents/stage10_document.txt",
            status=Document.Status.SUCCESS,
            paragraph_count=1,
        )
        Paragraph.objects.create(
            document=self.document,
            position=1,
            content="Knowledge Chat支持发布AI应用。",
            embedding=[0.2, 0.8],
        )

    def create_application(self, name="stage10_application"):
        response = self.client.post(
            "/api/applications/",
            {
                "name": name,
                "description": "stage10 test",
                "welcome_message": "欢迎使用",
                "suggested_questions": ["系统支持什么？"],
                "global_top_k": 5,
                "max_context_chars": 6000,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return Application.objects.get(pk=response.data["data"]["id"])

    def bind(self, application, knowledge=None):
        response = self.client.put(
            f"/api/applications/{application.id}/knowledge-bases/",
            {
                "knowledge_bases": [
                    {
                        "knowledge_base_id": (knowledge or self.knowledge).id,
                        "position": 1,
                        "weight": 1.0,
                        "enabled": True,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

    def publish(self, application):
        response = self.client.post(f"/api/applications/{application.id}/publish/")
        self.assertEqual(response.status_code, 200, response.data)
        application.refresh_from_db()
        return response

    def test_crud_and_owner_isolation(self):
        application = self.create_application()
        self.assertEqual(self.client.get("/api/applications/").data["data"]["total"], 1)
        self.assertEqual(
            self.other_client.get(f"/api/applications/{application.id}/").status_code,
            404,
        )
        response = self.client.patch(
            f"/api/applications/{application.id}/",
            {"description": "updated"},
            format="json",
        )
        self.assertEqual(response.data["data"]["description"], "updated")

    def test_name_unique_per_owner(self):
        self.create_application()
        response = self.client.post(
            "/api/applications/", {"name": "stage10_application"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_bind_other_owner_knowledge(self):
        application = self.create_application()
        response = self.client.put(
            f"/api/applications/{application.id}/knowledge-bases/",
            {
                "knowledge_bases": [
                    {
                        "knowledge_base_id": self.other_knowledge.id,
                        "position": 1,
                        "weight": 1,
                        "enabled": True,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(application.knowledge_links.exists())

    def test_maximum_five_knowledge_bases(self):
        application = self.create_application()
        items = [
            {"knowledge_base_id": self.knowledge.id, "position": index, "weight": 1, "enabled": True}
            for index in range(1, 7)
        ]
        response = self.client.put(
            f"/api/applications/{application.id}/knowledge-bases/",
            {"knowledge_bases": items},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_publish_snapshot_is_immutable_and_rollback(self):
        application = self.create_application()
        self.bind(application)
        first = self.publish(application).data["data"]
        self.assertEqual(first["version"], 1)
        self.assertNotIn("api_key", json.dumps(first["config_snapshot"]))
        self.client.patch(
            f"/api/applications/{application.id}/",
            {"system_prompt": "new draft"},
            format="json",
        )
        application.refresh_from_db()
        self.assertEqual(application.current_published_version.config_snapshot["system_prompt"], "")
        second = self.publish(application).data["data"]
        self.assertEqual(second["version"], 2)
        response = self.client.post(
            f"/api/applications/{application.id}/versions/{first['id']}/rollback/"
        )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.current_published_version.version, 1)

    def test_published_delete_requires_disable_and_keeps_knowledge(self):
        application = self.create_application()
        self.bind(application)
        self.publish(application)
        self.assertEqual(self.client.delete(f"/api/applications/{application.id}/").status_code, 409)
        self.assertEqual(
            self.client.post(f"/api/applications/{application.id}/disable/").status_code,
            200,
        )
        self.assertEqual(self.client.delete(f"/api/applications/{application.id}/").status_code, 200)
        self.assertTrue(KnowledgeBase.objects.filter(pk=self.knowledge.id).exists())
        self.assertTrue(Document.objects.filter(pk=self.document.id).exists())

    def test_bound_knowledge_base_delete_is_protected(self):
        application = self.create_application()
        self.bind(application)
        response = self.client.delete(f"/api/knowledge-bases/{self.knowledge.id}/")
        self.assertEqual(response.status_code, 409)

    def test_credential_plaintext_is_returned_once_and_not_stored(self):
        application = self.create_application()
        response = self.client.post(
            f"/api/applications/{application.id}/credentials/",
            {"name": "stage10 key"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        token = response.data["data"]["api_key"]
        credential = ApplicationCredential.objects.get(application=application)
        self.assertNotEqual(credential.secret_digest, token)
        self.assertNotIn(token, json.dumps(self.client.get(
            f"/api/applications/{application.id}/credentials/"
        ).data))
        self.assertEqual(authenticate_application_credential(token).id, credential.id)

    def test_public_token_visitor_and_conversation_isolation(self):
        application = self.create_application()
        self.bind(application)
        self.publish(application)
        enabled = self.client.post(f"/api/applications/{application.id}/public-access/enable/")
        token = enabled.data["data"]["public_token"]
        access = authenticate_public_token(token)
        self.assertNotEqual(access.token_digest, token)
        anonymous = APIClient()
        profile = anonymous.get(f"/api/public/applications/{token}/profile/")
        self.assertEqual(profile.status_code, 200)
        visitor_one = anonymous.post(f"/api/public/applications/{token}/visitor/").data["data"]["visitor_token"]
        visitor_two = anonymous.post(f"/api/public/applications/{token}/visitor/").data["data"]["visitor_token"]
        with patch(
            "api.application_views.retrieve_application_context"
        ) as retrieve, patch("api.application_views.stream_answer", return_value=iter(["回答"])):
            retrieve.return_value.references = [{
                "paragraph_id": 1,
                "document_id": 1,
                "document_name": "doc",
                "position": 1,
                "content": "内容",
                "similarity": 1.0,
                "knowledge_base_id": self.knowledge.id,
                "knowledge_base_name": self.knowledge.name,
            }]
            retrieve.return_value.latency_ms = 1
            response = anonymous.post(
                f"/api/public/applications/{token}/chat/stream/",
                {"message": "问题"},
                format="json",
                HTTP_X_VISITOR_TOKEN=visitor_one,
            )
            body = b"".join(response.streaming_content).decode()
        conversation_id = int(re.search(r'"conversation_id":\s*(\d+)', body).group(1))
        self.assertEqual(Message.objects.filter(conversation_id=conversation_id).count(), 2)
        denied = anonymous.get(
            f"/api/public/applications/{token}/conversations/{conversation_id}/messages/",
            HTTP_X_VISITOR_TOKEN=visitor_two,
        )
        self.assertEqual(denied.status_code, 404)

    def test_public_token_rotation_invalidates_old_token(self):
        application = self.create_application()
        self.bind(application)
        self.publish(application)
        old = self.client.post(
            f"/api/applications/{application.id}/public-access/enable/"
        ).data["data"]["public_token"]
        new = self.client.post(
            f"/api/applications/{application.id}/public-access/rotate/"
        ).data["data"]["public_token"]
        anonymous = APIClient()
        self.assertEqual(anonymous.get(f"/api/public/applications/{old}/profile/").status_code, 404)
        self.assertEqual(anonymous.get(f"/api/public/applications/{new}/profile/").status_code, 200)

    def test_embed_response_has_frame_ancestor_policy_and_escaped_title(self):
        application = self.create_application("stage10 <script>alert(1)</script>")
        self.bind(application)
        self.publish(application)
        token = self.client.post(
            f"/api/applications/{application.id}/public-access/enable/"
        ).data["data"]["public_token"]
        self.client.patch(
            f"/api/applications/{application.id}/public-access/",
            {"allowed_frame_origins": ["https://portal.example.com"]},
            format="json",
        )
        response = APIClient().get(f"/api/public/applications/{token}/embed/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors https://portal.example.com", response["Content-Security-Policy"])
        self.assertNotIn("<script>alert(1)</script>", response.content.decode())

    def test_credential_enabled_requires_real_boolean(self):
        application = self.create_application()
        created = self.client.post(
            f"/api/applications/{application.id}/credentials/", {"name": "key"}, format="json"
        ).data["data"]
        response = self.client.patch(
            f"/api/applications/{application.id}/credentials/{created['id']}/",
            {"enabled": "false"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(ApplicationCredential.objects.get(pk=created["id"]).enabled)

    def test_application_agent_preview_reuses_agent_executor(self):
        application = self.create_application()
        application.agent_enabled = True
        application.enabled_tools = []
        application.save(update_fields=["agent_enabled", "enabled_tools"])
        self.bind(application)
        chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="应用Agent回答", tool_calls=None))]
        )
        client = MagicMock()
        client.chat.completions.create.return_value = [chunk]
        context = MagicMock()
        context.__enter__.return_value = client
        context.__exit__.return_value = False
        with patch(
            "api.services.agent_executor.resolve_chat_config",
            return_value=SimpleNamespace(model_name="stage10-agent"),
        ), patch("api.services.agent_executor.create_openai_client", return_value=context):
            response = self.client.post(
                f"/api/applications/{application.id}/preview/chat/stream/",
                {"message": "执行Agent"},
                format="json",
            )
            body = b"".join(response.streaming_content).decode()
        self.assertIn("应用Agent回答", body)
        self.assertIn("event: agent_start", body)
        self.assertEqual(AgentRun.objects.get().status, AgentRun.Status.SUCCESS)
        conversation = Conversation.objects.get(application=application)
        self.assertEqual(conversation.messages.count(), 2)

    def test_application_agent_api_supports_non_stream_response(self):
        application = self.create_application()
        application.agent_enabled = True
        application.enabled_tools = []
        application.save(update_fields=["agent_enabled", "enabled_tools"])
        self.bind(application)
        self.publish(application)
        key = self.client.post(
            f"/api/applications/{application.id}/credentials/", {"name": "agent-api"}, format="json"
        ).data["data"]["api_key"]
        chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Agent API回答", tool_calls=None))]
        )
        model_client = MagicMock()
        model_client.chat.completions.create.return_value = [chunk]
        context = MagicMock()
        context.__enter__.return_value = model_client
        context.__exit__.return_value = False
        with patch(
            "api.services.agent_executor.resolve_chat_config",
            return_value=SimpleNamespace(model_name="stage10-agent"),
        ), patch("api.services.agent_executor.create_openai_client", return_value=context):
            response = APIClient().post(
                f"/api/v1/applications/{application.id}/chat/completions",
                {"messages": [{"role": "user", "content": "执行Agent"}], "stream": False},
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {key}",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["choices"][0]["message"]["content"], "Agent API回答")
        self.assertEqual(AgentRun.objects.get().status, AgentRun.Status.SUCCESS)

    def test_api_credential_cannot_call_other_application(self):
        first = self.create_application("stage10_first")
        second = self.create_application("stage10_second")
        response = self.client.post(
            f"/api/applications/{first.id}/credentials/", {"name": "key"}, format="json"
        )
        token = response.data["data"]["api_key"]
        anonymous = APIClient()
        denied = anonymous.post(
            f"/api/v1/applications/{second.id}/chat/completions",
            {"messages": [{"role": "user", "content": "test"}]},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(denied.status_code, 401)

    def test_access_log_serializer_never_contains_tokens(self):
        application = self.create_application()
        ApplicationAccessLog.objects.create(
            application=application,
            request_id="stage10_request",
            access_type=Conversation.AccessType.PREVIEW,
        )
        body = json.dumps(
            self.client.get(f"/api/applications/{application.id}/access-logs/").data
        )
        self.assertNotIn("secret", body)
        self.assertNotIn("authorization", body.lower())

    def test_web_log_filter_redacts_public_and_api_tokens(self):
        public_token = "kc_pub_1234567890ab_stage10TemporaryPublicSecret"
        api_key = "kc_app_abcdef123456_stage10TemporaryApplicationSecret"
        record = logging.LogRecord(
            "django.server",
            logging.INFO,
            __file__,
            1,
            'GET /api/public/applications/%s/profile/ Authorization=%s',
            (public_token, api_key),
            None,
        )
        self.assertTrue(ApplicationSecretFilter().filter(record))
        rendered = record.getMessage()
        self.assertNotIn(public_token, rendered)
        self.assertNotIn(api_key, rendered)
        self.assertIn("kc_pub_<redacted>", rendered)

    def test_weighted_cross_knowledge_rrf_and_global_top_k(self):
        second_knowledge = KnowledgeBase.objects.create(owner=self.user, name="stage10_second_kb")
        application = self.create_application()
        ApplicationKnowledgeBase.objects.create(
            application=application, knowledge_base=self.knowledge, position=1, weight=1
        )
        ApplicationKnowledgeBase.objects.create(
            application=application, knowledge_base=second_knowledge, position=2, weight=3
        )
        application.global_top_k = 1
        application.save(update_fields=["global_top_k"])
        runtime = resolve_draft_runtime(application)

        def result_for(kb, *_args, **_kwargs):
            item = {
                "paragraph_id": kb.id * 10,
                "document_id": kb.id,
                "document_name": kb.name,
                "position": 1,
                "content": kb.name,
                "similarity": 1,
            }
            return type("Result", (), {"references": lambda self: [item]})()

        with patch("api.services.application_retrieval.retrieve_candidates", side_effect=result_for):
            result = retrieve_application_context(runtime, "query")
        self.assertEqual(len(result.references), 1)
        self.assertEqual(result.references[0]["knowledge_base_id"], second_knowledge.id)

    def test_rate_limit_returns_retry(self):
        for _ in range(2):
            enforce_rate_limit("stage10-limit", 2)
        with self.assertRaises(RateLimitExceeded) as raised:
            enforce_rate_limit("stage10-limit", 2)
        self.assertGreaterEqual(raised.exception.retry_after, 1)
