import json
import math
import os
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from .models import (
    AgentRun,
    Conversation,
    Document,
    DocumentProcessingTask,
    KnowledgeBase,
    Message,
    ModelConfig,
    Paragraph,
    ToolExecution,
)
from .services.agent_executor import stream_agent_run
from .services.agent_persistence import create_agent_run
from .services.agent_tools.calculator import calculate
from .services.agent_tools.context import ToolContext
from .services.agent_tools.definitions import ToolDefinition, ToolResult
from .services.agent_tools.exceptions import ToolRejectedError
from .services.agent_tools.registry import get_tool
from .services.model_clients import ModelServiceError
from .services.embeddings import cosine_similarity
from .services.keyword_retrieval import calculate_bm25_scores
from .services.retrieval import normalize_vector_score, retrieve_candidates
from .services.retrieval_evaluation import (
    EvaluationDataError,
    content_sha256,
    evaluate_ranking,
    load_evaluation_cases,
)
from .services.retrieval_tokenizer import tokenize_text
from .services.reranker import RerankOutcome
from .services.rrf import reciprocal_rank_fusion


TEST_MODEL_ENCRYPTION_KEY = "1iXm-xL4o7Zs6fpxlFW7VIxtrML_aegHvXTIqsU3M5k="


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class KnowledgeFlowTests(APITestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "LLM_MODEL": "", "EMBEDDING_MODEL": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.user = User.objects.create_user(username="tester", password="test-password")
        self.client = APIClient()

    def login(self):
        response = self.client.post("/api/login/", {"username": "tester", "password": "test-password"})
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['data']['token']}")

    def upload_document(self, knowledge, name="document.txt", content="第一段知识。第二段知识。"):
        upload = SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/plain")
        response = self.client.post(f"/api/knowledge-bases/{knowledge.id}/documents/", {"file": upload})
        self.assertEqual(response.status_code, 202)
        return Document.objects.get(pk=response.data["data"]["document"]["id"])

    def stream_chat(self, knowledge, message, conversation_id=None):
        response = self.client.post(
            f"/api/knowledge-bases/{knowledge.id}/chat/stream/",
            {"message": message, "conversation_id": conversation_id},
            format="json",
        )
        body = b"".join(response.streaming_content).decode("utf-8") if response.streaming else ""
        return response, body

    def test_complete_local_rag_flow(self):
        self.login()
        response = self.client.post(
            "/api/knowledge-bases/", {"name": "Python资料", "description": "测试知识库"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        knowledge_id = response.data["data"]["id"]

        upload = SimpleUploadedFile(
            "python.txt",
            "Python 使用缩进组织代码块。Django 是一个 Python Web 框架。".encode("utf-8"),
            content_type="text/plain",
        )
        response = self.client.post(f"/api/knowledge-bases/{knowledge_id}/documents/", {"file": upload})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["data"]["document"]["status"], Document.Status.SUCCESS)
        self.assertEqual(response.data["data"]["task"]["status"], DocumentProcessingTask.Status.SUCCESS)
        self.assertTrue(Paragraph.objects.filter(document__knowledge_base_id=knowledge_id).exists())

        response = self.client.post(
            f"/api/knowledge-bases/{knowledge_id}/search/", {"query": "Django是什么？", "top_k": 3}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"][0]["document_name"], "python.txt")

        response = self.client.post(
            f"/api/knowledge-bases/{knowledge_id}/chat/stream/",
            {"message": "Django是什么？"},
            format="json",
        )
        body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("event: content", body)
        self.assertIn("event: references", body)
        self.assertIn("event: done", body)

    def test_user_cannot_access_another_users_knowledge_base(self):
        other = User.objects.create_user(username="other", password="password")
        knowledge = KnowledgeBase.objects.create(name="私有资料", owner=other)
        self.login()

        response = self.client.get(f"/api/knowledge-bases/{knowledge.id}/")
        self.assertEqual(response.status_code, 404)

        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/",
            {"name": "越权修改"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        knowledge.refresh_from_db()
        self.assertEqual(knowledge.name, "私有资料")

        response = self.client.delete(f"/api/knowledge-bases/{knowledge.id}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(KnowledgeBase.objects.filter(id=knowledge.id).exists())

        response = self.client.get(f"/api/knowledge-bases/{knowledge.id}/documents/")
        self.assertEqual(response.status_code, 404)

    def test_knowledge_base_detail_and_partial_update(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(
            name="原名称",
            description="原描述",
            owner=self.user,
        )

        response = self.client.get(f"/api/knowledge-bases/{knowledge.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["name"], "原名称")

        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/",
            {"name": "  新名称  "},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        knowledge.refresh_from_db()
        self.assertEqual(knowledge.name, "新名称")
        self.assertEqual(knowledge.description, "原描述")

        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/",
            {"name": "   "},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        knowledge.refresh_from_db()
        self.assertEqual(knowledge.name, "新名称")

    def test_knowledge_base_search_and_pagination(self):
        self.login()
        KnowledgeBase.objects.create(name="Python基础", description="入门", owner=self.user)
        KnowledgeBase.objects.create(name="Django项目", description="Python Web", owner=self.user)
        KnowledgeBase.objects.create(name="Go基础", description="后端开发", owner=self.user)
        KnowledgeBase.objects.create(name="算法", description="面试题", owner=self.user)
        KnowledgeBase.objects.create(name="数据库", description="SQL", owner=self.user)
        other = User.objects.create_user(username="list-other", password="password")
        KnowledgeBase.objects.create(name="Python私有资料", description="不能返回", owner=other)

        response = self.client.get("/api/knowledge-bases/?keyword=Python")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 2)
        self.assertEqual(
            {item["name"] for item in response.data["data"]["items"]},
            {"Python基础", "Django项目"},
        )

        response = self.client.get("/api/knowledge-bases/?page=2&page_size=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 5)
        self.assertEqual(response.data["data"]["page"], 2)
        self.assertEqual(response.data["data"]["page_size"], 2)
        self.assertEqual(response.data["data"]["total_pages"], 3)
        self.assertEqual(len(response.data["data"]["items"]), 2)

        response = self.client.get("/api/knowledge-bases/?page_size=invalid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["page_size"], 12)

    def test_document_resources_require_owner_and_matching_knowledge_base(self):
        self.login()
        own_knowledge = KnowledgeBase.objects.create(name="自己的知识库", owner=self.user)
        other_own_knowledge = KnowledgeBase.objects.create(name="另一个自己的知识库", owner=self.user)
        own_document = self.upload_document(own_knowledge)

        other_user = User.objects.create_user(username="document-other", password="password")
        other_knowledge = KnowledgeBase.objects.create(name="他人的知识库", owner=other_user)
        other_document = Document.objects.create(
            knowledge_base=other_knowledge,
            name="private.txt",
            file=SimpleUploadedFile("private.txt", b"private"),
        )

        response = self.client.get(
            f"/api/knowledge-bases/{own_knowledge.id}/documents/{own_document.id}/"
        )
        self.assertEqual(response.status_code, 200)

        invalid_targets = [
            ("get", f"/api/knowledge-bases/{other_own_knowledge.id}/documents/{own_document.id}/"),
            (
                "get",
                f"/api/knowledge-bases/{other_own_knowledge.id}/documents/{own_document.id}/paragraphs/",
            ),
            (
                "post",
                f"/api/knowledge-bases/{other_own_knowledge.id}/documents/{own_document.id}/reprocess/",
            ),
            ("delete", f"/api/knowledge-bases/{other_own_knowledge.id}/documents/{own_document.id}/"),
            ("get", f"/api/knowledge-bases/{other_knowledge.id}/documents/{other_document.id}/"),
            (
                "get",
                f"/api/knowledge-bases/{other_knowledge.id}/documents/{other_document.id}/paragraphs/",
            ),
            (
                "post",
                f"/api/knowledge-bases/{other_knowledge.id}/documents/{other_document.id}/reprocess/",
            ),
            ("delete", f"/api/knowledge-bases/{other_knowledge.id}/documents/{other_document.id}/"),
        ]
        for method, url in invalid_targets:
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 404, f"{method.upper()} {url}")

        self.assertTrue(Document.objects.filter(pk=own_document.id).exists())
        self.assertTrue(Document.objects.filter(pk=other_document.id).exists())

    def test_paragraphs_are_ordered_paginated_and_do_not_expose_embeddings(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="分页知识库", owner=self.user)
        document = Document.objects.create(
            knowledge_base=knowledge,
            name="ordered.txt",
            file=SimpleUploadedFile("ordered.txt", b"ordered"),
            status=Document.Status.SUCCESS,
            paragraph_count=5,
        )
        for position in [5, 1, 4, 2, 3]:
            Paragraph.objects.create(
                document=document,
                position=position,
                content=f"切片 {position}",
                embedding=[float(position)],
            )

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/documents/{document.id}/paragraphs/?page=2&page_size=2"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["position"] for item in response.data["data"]["items"]], [3, 4])
        self.assertEqual(response.data["data"]["total"], 5)
        self.assertEqual(response.data["data"]["page"], 2)
        self.assertEqual(response.data["data"]["page_size"], 2)
        self.assertEqual(response.data["data"]["total_pages"], 3)
        self.assertNotIn("embedding", response.data["data"]["items"][0])

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/documents/{document.id}/paragraphs/?page_size=500"
        )
        self.assertEqual(response.data["data"]["page_size"], 100)

    def test_document_delete_removes_database_rows_and_physical_file(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="删除文档知识库", owner=self.user)
        document = self.upload_document(knowledge, "delete-me.txt", "需要被删除的内容")
        document_id = document.id
        file_path = Path(document.file.path)
        self.assertTrue(file_path.exists())
        self.assertTrue(Paragraph.objects.filter(document=document).exists())

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                f"/api/knowledge-bases/{knowledge.id}/documents/{document.id}/"
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document_id).exists())
        self.assertFalse(Paragraph.objects.filter(document_id=document_id).exists())
        self.assertFalse(file_path.exists())

        response = self.client.delete(
            f"/api/knowledge-bases/{knowledge.id}/documents/{document_id}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_knowledge_base_cascade_delete_also_removes_physical_file(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="级联删除知识库", owner=self.user)
        document = self.upload_document(knowledge, "cascade-delete.txt", "级联删除内容")
        document_id = document.id
        file_path = Path(document.file.path)
        self.assertTrue(file_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(f"/api/knowledge-bases/{knowledge.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document_id).exists())
        self.assertFalse(Paragraph.objects.filter(document_id=document_id).exists())
        self.assertFalse(file_path.exists())

    def test_reprocess_replaces_paragraphs_instead_of_appending(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="重新处理知识库", owner=self.user)
        document = self.upload_document(knowledge, "reprocess.txt", "重新处理使用的知识内容")
        old_ids = set(document.paragraphs.values_list("id", flat=True))
        Paragraph.objects.create(
            document=document,
            position=document.paragraphs.count() + 1,
            content="不应保留的旧切片",
            embedding=[0.0],
        )

        response = self.client.post(
            f"/api/knowledge-bases/{knowledge.id}/documents/{document.id}/reprocess/"
        )
        self.assertEqual(response.status_code, 202)
        document.refresh_from_db()
        new_ids = set(document.paragraphs.values_list("id", flat=True))
        self.assertTrue(old_ids.isdisjoint(new_ids))
        self.assertEqual(document.paragraph_count, 1)
        self.assertEqual(document.paragraphs.count(), 1)
        self.assertEqual(list(document.paragraphs.values_list("position", flat=True)), [1])
        self.assertFalse(document.paragraphs.filter(content="不应保留的旧切片").exists())

    def test_reprocess_failure_preserves_existing_usable_paragraphs(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="失败保留知识库", owner=self.user)
        document = self.upload_document(knowledge, "keep-old.txt", "仍然可以检索的旧内容")
        before = list(document.paragraphs.values_list("id", "position", "content", "embedding"))

        with patch(
            "api.services.document_processor.embed_texts",
            side_effect=RuntimeError("向量服务暂时不可用"),
        ):
            response = self.client.post(
                f"/api/knowledge-bases/{knowledge.id}/documents/{document.id}/reprocess/"
            )

        self.assertEqual(response.status_code, 202)
        document.refresh_from_db()
        after = list(document.paragraphs.values_list("id", "position", "content", "embedding"))
        self.assertEqual(after, before)
        self.assertEqual(document.status, Document.Status.SUCCESS)
        self.assertEqual(document.paragraph_count, len(before))
        self.assertIn("已保留旧切片", document.error_message)
        self.assertEqual(response.data["data"]["task"]["status"], DocumentProcessingTask.Status.FAILURE)
        self.assertNotIn("向量服务暂时不可用", str(response.data))

    def test_reprocess_failure_without_old_paragraphs_marks_document_failure(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="首次失败知识库", owner=self.user)
        document = Document.objects.create(
            knowledge_base=knowledge,
            name="no-old.txt",
            file=SimpleUploadedFile("no-old.txt", "可读取但向量失败".encode("utf-8")),
        )

        with patch(
            "api.services.document_processor.embed_texts",
            side_effect=RuntimeError("向量生成失败"),
        ):
            response = self.client.post(
                f"/api/knowledge-bases/{knowledge.id}/documents/{document.id}/reprocess/"
            )

        self.assertEqual(response.status_code, 202)
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILURE)
        self.assertEqual(document.paragraph_count, 0)
        self.assertFalse(document.paragraphs.exists())
        self.assertEqual(response.data["data"]["task"]["status"], DocumentProcessingTask.Status.FAILURE)
        self.assertEqual(document.error_message, "文档处理失败，请稍后重试")

    def test_upload_rejects_unsupported_and_empty_files_with_clear_errors(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="格式校验知识库", owner=self.user)

        unsupported = SimpleUploadedFile("unsupported.xlsx", b"not-an-xlsx")
        response = self.client.post(
            f"/api/knowledge-bases/{knowledge.id}/documents/", {"file": unsupported}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("仅支持", response.data["message"])
        self.assertFalse(knowledge.documents.exists())

        empty = SimpleUploadedFile("empty.txt", b"", content_type="text/plain")
        response = self.client.post(
            f"/api/knowledge-bases/{knowledge.id}/documents/", {"file": empty}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["data"]["task"]["status"], DocumentProcessingTask.Status.FAILURE)
        self.assertIn("文档没有可提取的文字", response.data["data"]["task"]["error_message"])
        failed_document = knowledge.documents.get(name="empty.txt")
        self.assertEqual(failed_document.status, Document.Status.FAILURE)
        self.assertEqual(failed_document.paragraph_count, 0)

    def test_conversation_list_is_paginated_summary_and_sorted_by_latest_message(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-会话列表", owner=self.user)
        first = Conversation.objects.create(knowledge_base=knowledge, owner=self.user, title="较早会话")
        second = Conversation.objects.create(knowledge_base=knowledge, owner=self.user, title="较新会话")
        older_message = Message.objects.create(
            conversation=second,
            role=Message.Role.USER,
            content="第二个会话的问题",
        )
        latest_message = Message.objects.create(
            conversation=first,
            role=Message.Role.USER,
            content="第一个会话的最新问题",
        )
        base_time = timezone.now()
        Message.objects.filter(pk=older_message.id).update(created_at=base_time)
        Message.objects.filter(pk=latest_message.id).update(created_at=base_time + timedelta(seconds=1))

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/conversations/?page=1&page_size=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 2)
        self.assertEqual(response.data["data"]["total_pages"], 2)
        self.assertEqual(response.data["data"]["items"][0]["id"], first.id)
        self.assertEqual(response.data["data"]["items"][0]["message_count"], 1)
        self.assertIsNotNone(response.data["data"]["items"][0]["last_message_at"])
        self.assertNotIn("messages", response.data["data"]["items"][0])

    def test_conversation_detail_rename_and_delete_cascades_messages(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-会话管理", owner=self.user)
        conversation = Conversation.objects.create(
            knowledge_base=knowledge,
            owner=self.user,
            title="原始标题",
        )
        message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="需要级联删除的消息",
        )

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["title"], "原始标题")

        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/",
            {"title": "  手动修改的标题  "},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, "手动修改的标题")

        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/",
            {"title": "   "},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/",
            {"title": "超" * 101},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, "手动修改的标题")

        response = self.client.delete(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Conversation.objects.filter(pk=conversation.id).exists())
        self.assertFalse(Message.objects.filter(pk=message.id).exists())

    def test_conversation_resources_reject_other_owner_and_mismatched_knowledge(self):
        self.login()
        own_knowledge = KnowledgeBase.objects.create(name="stage04-自己的知识库", owner=self.user)
        other_own_knowledge = KnowledgeBase.objects.create(name="stage04-错误知识库", owner=self.user)
        own_conversation = Conversation.objects.create(
            knowledge_base=own_knowledge,
            owner=self.user,
            title="自己的会话",
        )
        other_user = User.objects.create_user(username="conversation-other", password="password")
        other_knowledge = KnowledgeBase.objects.create(name="stage04-他人知识库", owner=other_user)
        other_conversation = Conversation.objects.create(
            knowledge_base=other_knowledge,
            owner=other_user,
            title="他人的会话",
        )
        self.assertEqual(
            self.client.get(f"/api/knowledge-bases/{other_knowledge.id}/conversations/").status_code,
            404,
        )

        invalid_targets = [
            (other_own_knowledge.id, own_conversation.id),
            (other_knowledge.id, other_conversation.id),
        ]
        for knowledge_id, conversation_id in invalid_targets:
            detail_url = f"/api/knowledge-bases/{knowledge_id}/conversations/{conversation_id}/"
            self.assertEqual(self.client.get(detail_url).status_code, 404)
            self.assertEqual(
                self.client.patch(detail_url, {"title": "越权修改"}, format="json").status_code,
                404,
            )
            self.assertEqual(self.client.delete(detail_url).status_code, 404)
            self.assertEqual(
                self.client.get(f"{detail_url}messages/").status_code,
                404,
            )

        own_conversation.refresh_from_db()
        other_conversation.refresh_from_db()
        self.assertEqual(own_conversation.title, "自己的会话")
        self.assertEqual(other_conversation.title, "他人的会话")

    def test_conversation_messages_are_ordered_and_support_latest_page(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-消息分页", owner=self.user)
        conversation = Conversation.objects.create(
            knowledge_base=knowledge,
            owner=self.user,
            title="长会话",
        )
        for index in range(1, 56):
            Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER if index % 2 else Message.Role.ASSISTANT,
                content=f"消息-{index:02d}",
                references=[] if index % 2 else [{"document_name": "test.txt"}],
            )

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/messages/"
            "?page=last&page_size=50"
        )

        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["total"], 55)
        self.assertEqual(data["page"], 2)
        self.assertEqual(data["total_pages"], 2)
        self.assertTrue(data["has_previous"])
        self.assertEqual(data["previous_page"], 1)
        self.assertEqual(data["items"][0]["content"], "消息-51")
        self.assertEqual(data["items"][-1]["content"], "消息-55")
        self.assertEqual(data["items"][-1]["references"], [])

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/messages/"
            "?page=1&page_size=500"
        )
        self.assertEqual(response.data["data"]["page_size"], 100)
        self.assertEqual(response.data["data"]["items"][0]["content"], "消息-01")

    def test_knowledge_base_delete_cascades_conversations_and_messages(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-级联会话", owner=self.user)
        conversation = Conversation.objects.create(
            knowledge_base=knowledge,
            owner=self.user,
            title="会被知识库级联删除",
        )
        message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="会被级联删除的消息",
        )

        response = self.client.delete(f"/api/knowledge-bases/{knowledge.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Conversation.objects.filter(pk=conversation.id).exists())
        self.assertFalse(Message.objects.filter(pk=message.id).exists())

    def test_stream_chat_creates_titled_conversation_and_persists_answer_references(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-流式持久化", owner=self.user)
        self.upload_document(knowledge, "stage04-rag.txt", "RAG 是检索增强生成。")

        response, body = self.stream_chat(knowledge, "  请   解释\nRAG  ")

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: meta", body)
        self.assertIn("event: done", body)
        conversation = Conversation.objects.get(knowledge_base=knowledge, owner=self.user)
        self.assertEqual(conversation.title, "请 解释 RAG")
        messages = list(conversation.messages.order_by("created_at", "id"))
        self.assertEqual([message.role for message in messages], [Message.Role.USER, Message.Role.ASSISTANT])
        self.assertEqual(messages[0].content, "请   解释\nRAG")
        self.assertTrue(messages[1].content)
        self.assertEqual(messages[1].references[0]["document_name"], "stage04-rag.txt")

    def test_follow_up_uses_existing_conversation_without_overwriting_manual_title(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-连续对话", owner=self.user)
        _, first_body = self.stream_chat(knowledge, "第一条问题")
        self.assertIn("event: done", first_body)
        conversation = Conversation.objects.get(knowledge_base=knowledge, owner=self.user)

        response = self.client.patch(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/",
            {"title": "我手动设置的标题"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        response, second_body = self.stream_chat(knowledge, "第二条问题", conversation.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: done", second_body)
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, "我手动设置的标题")
        self.assertEqual(conversation.messages.count(), 4)

    def test_stream_chat_rejects_forged_or_invalid_conversation_id_without_creating_data(self):
        self.login()
        own_knowledge = KnowledgeBase.objects.create(name="stage04-合法聊天知识库", owner=self.user)
        other_own_knowledge = KnowledgeBase.objects.create(name="stage04-另一个知识库", owner=self.user)
        own_conversation = Conversation.objects.create(
            knowledge_base=own_knowledge,
            owner=self.user,
            title="合法会话",
        )
        other_user = User.objects.create_user(username="chat-other", password="password")
        other_knowledge = KnowledgeBase.objects.create(name="stage04-他人聊天知识库", owner=other_user)
        other_conversation = Conversation.objects.create(
            knowledge_base=other_knowledge,
            owner=other_user,
            title="他人会话",
        )
        conversation_count = Conversation.objects.count()
        message_count = Message.objects.count()

        for conversation_id in [other_conversation.id, "invalid"]:
            response = self.client.post(
                f"/api/knowledge-bases/{own_knowledge.id}/chat/stream/",
                {"message": "伪造会话测试", "conversation_id": conversation_id},
                format="json",
            )
            self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/api/knowledge-bases/{other_own_knowledge.id}/chat/stream/",
            {"message": "错误知识库测试", "conversation_id": own_conversation.id},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Conversation.objects.count(), conversation_count)
        self.assertEqual(Message.objects.count(), message_count)

    def test_stream_failure_keeps_user_message_but_does_not_save_partial_assistant(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-流式失败", owner=self.user)

        def failing_answer(knowledge_base, question, references):
            yield "部分回答"
            raise RuntimeError("模型连接中断")

        with patch("api.views.stream_answer", side_effect=failing_answer):
            response, body = self.stream_chat(knowledge, "失败时保留什么？")

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: content", body)
        self.assertIn("event: error", body)
        self.assertIn("模型回答生成失败，请稍后重试", body)
        conversation = Conversation.objects.get(knowledge_base=knowledge, owner=self.user)
        self.assertEqual(conversation.messages.filter(role=Message.Role.USER).count(), 1)
        self.assertFalse(conversation.messages.filter(role=Message.Role.ASSISTANT).exists())

    def test_empty_stream_does_not_create_empty_assistant_message(self):
        self.login()
        knowledge = KnowledgeBase.objects.create(name="stage04-空回答", owner=self.user)

        with patch("api.views.stream_answer", return_value=iter(())):
            response, body = self.stream_chat(knowledge, "空回答测试")

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: error", body)
        self.assertIn("模型回答生成失败，请稍后重试", body)
        conversation = Conversation.objects.get(knowledge_base=knowledge, owner=self.user)
        self.assertEqual(conversation.messages.count(), 1)
        self.assertEqual(conversation.messages.first().role, Message.Role.USER)


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(),
    MODEL_CONFIG_ENCRYPTION_KEY=TEST_MODEL_ENCRYPTION_KEY,
    DEBUG=True,
    ALLOW_PRIVATE_MODEL_ENDPOINTS=True,
)
class ModelConfigurationTests(APITestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "LLM_MODEL": "", "EMBEDDING_MODEL": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.user = User.objects.create_user(username="stage05-user", password="test-password")
        self.other_user = User.objects.create_user(
            username="stage05-other", password="test-password"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_config(self, name="stage05-chat", model_type=ModelConfig.ModelType.CHAT):
        response = self.client.post(
            "/api/model-configs/",
            {
                "name": name,
                "model_type": model_type,
                "base_url": "http://127.0.0.1:18080/v1/",
                "api_key": "stage05-temporary-secret-ABCD",
                "model_name": "stage05-model",
                "timeout_seconds": 12,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return ModelConfig.objects.get(pk=response.data["data"]["id"]), response

    def test_crud_encrypts_masks_preserves_and_rotates_key_without_leaking_ciphertext(self):
        config, response = self.create_config()
        self.assertNotEqual(config.encrypted_api_key, "stage05-temporary-secret-ABCD")
        self.assertNotIn("stage05-temporary-secret-ABCD", str(response.data))
        self.assertNotIn("encrypted_api_key", response.data["data"])
        self.assertEqual(response.data["data"]["api_key_masked"], "••••••••ABCD")
        original_ciphertext = config.encrypted_api_key

        response = self.client.patch(
            f"/api/model-configs/{config.id}/",
            {"name": "stage05-chat-renamed", "timeout_seconds": 20, "api_key": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.encrypted_api_key, original_ciphertext)
        self.assertEqual(config.revision, 1)

        response = self.client.patch(
            f"/api/model-configs/{config.id}/",
            {"api_key": "stage05-rotated-secret-WXYZ", "model_name": "stage05-model-v2"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertNotEqual(config.encrypted_api_key, original_ciphertext)
        self.assertNotEqual(config.encrypted_api_key, "stage05-rotated-secret-WXYZ")
        self.assertEqual(config.revision, 2)
        self.assertEqual(response.data["data"]["api_key_masked"], "••••••••WXYZ")

        response = self.client.patch(
            f"/api/model-configs/{config.id}/",
            {"api_key": "••••••••WXYZ"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(MODEL_CONFIG_ENCRYPTION_KEY="")
    def test_missing_master_key_fails_closed_without_plaintext_storage(self):
        response = self.client.post(
            "/api/model-configs/",
            {
                "name": "stage05-no-master-key",
                "model_type": "CHAT",
                "base_url": "http://127.0.0.1:18080/v1",
                "api_key": "stage05-must-not-be-stored",
                "model_name": "test-model",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ModelConfig.objects.filter(name="stage05-no-master-key").exists())
        self.assertNotIn("stage05-must-not-be-stored", str(response.data))

    def test_owner_isolation_covers_list_detail_patch_test_and_delete(self):
        config, _ = self.create_config()
        other_client = APIClient()
        other_client.force_authenticate(self.other_user)
        self.assertEqual(other_client.get("/api/model-configs/").data["data"]["total"], 0)
        url = f"/api/model-configs/{config.id}/"
        self.assertEqual(other_client.get(url).status_code, 404)
        self.assertEqual(other_client.patch(url, {"name": "stolen"}, format="json").status_code, 404)
        self.assertEqual(other_client.post(f"{url}test/").status_code, 404)
        self.assertEqual(other_client.delete(url).status_code, 404)
        config.refresh_from_db()
        self.assertEqual(config.name, "stage05-chat")

    def test_selection_validates_owner_and_type_and_protects_used_config(self):
        chat, _ = self.create_config("stage05-chat-select", ModelConfig.ModelType.CHAT)
        embedding, _ = self.create_config(
            "stage05-embedding-select", ModelConfig.ModelType.EMBEDDING
        )
        other_config = ModelConfig.objects.create(
            owner=self.other_user,
            name="stage05-other-config",
            model_type=ModelConfig.ModelType.CHAT,
            base_url="http://127.0.0.1:18080/v1",
            model_name="other-model",
            encrypted_api_key="not-decrypted-in-this-test",
        )
        knowledge = KnowledgeBase.objects.create(name="stage05-selection", owner=self.user)
        url = f"/api/knowledge-bases/{knowledge.id}/model-config/"

        for payload in [
            {"chat_model_config_id": embedding.id},
            {"embedding_model_config_id": chat.id},
            {"chat_model_config_id": other_config.id},
        ]:
            self.assertEqual(self.client.patch(url, payload, format="json").status_code, 400)

        response = self.client.patch(
            url,
            {"chat_model_config_id": chat.id, "embedding_model_config_id": embedding.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["chat_model_config_id"], chat.id)
        self.assertEqual(response.data["data"]["embedding_model_config_id"], embedding.id)
        self.assertEqual(self.client.delete(f"/api/model-configs/{chat.id}/").status_code, 409)

        self.client.delete(f"/api/knowledge-bases/{knowledge.id}/")
        self.assertTrue(ModelConfig.objects.filter(pk=chat.id).exists())
        self.assertTrue(ModelConfig.objects.filter(pk=embedding.id).exists())

    @override_settings(DEBUG=False, ALLOW_PRIVATE_MODEL_ENDPOINTS=False)
    def test_ssrf_validation_rejects_unsafe_schemes_credentials_and_private_addresses(self):
        unsafe_urls = [
            "ftp://example.com/v1",
            "https://user:password@example.com/v1",
            "https://example.com/v1?token=value",
            "https://example.com/v1#fragment",
            "http://127.0.0.1:8000/v1",
            "https://127.0.0.1/v1",
            "https://[::1]/v1",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/v1",
        ]
        for index, base_url in enumerate(unsafe_urls):
            response = self.client.post(
                "/api/model-configs/",
                {
                    "name": f"stage05-unsafe-{index}",
                    "model_type": "CHAT",
                    "base_url": base_url,
                    "api_key": "stage05-unsafe-key",
                    "model_name": "model",
                },
                format="json",
            )
            self.assertEqual(response.status_code, 400, base_url)
        self.assertFalse(ModelConfig.objects.filter(name__startswith="stage05-unsafe").exists())

    def test_connectivity_tests_chat_and_embedding_without_creating_conversations(self):
        chat, _ = self.create_config("stage05-test-chat", ModelConfig.ModelType.CHAT)
        embedding, _ = self.create_config(
            "stage05-test-embedding", ModelConfig.ModelType.EMBEDDING
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = SimpleNamespace(choices=[object()])
        mock_client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
        )
        context = MagicMock()
        context.__enter__.return_value = mock_client
        context.__exit__.return_value = False

        with patch("api.services.model_connectivity.create_openai_client", return_value=context):
            chat_response = self.client.post(f"/api/model-configs/{chat.id}/test/")
            embedding_response = self.client.post(f"/api/model-configs/{embedding.id}/test/")

        self.assertEqual(chat_response.status_code, 200)
        self.assertIsNone(chat_response.data["data"]["embedding_dimension"])
        self.assertEqual(embedding_response.status_code, 200)
        self.assertEqual(embedding_response.data["data"]["embedding_dimension"], 3)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_connectivity_failure_returns_only_safe_mapped_messages(self):
        config, _ = self.create_config("stage05-safe-error")
        cases = [
            ("AUTH_FAILED", "API Key无效或没有模型权限"),
            ("TIMEOUT", "模型服务响应超时"),
            ("NOT_FOUND", "模型地址或模型名称不存在"),
            ("RATE_LIMITED", "请求频率过高、额度不足或服务限流"),
            ("CONNECTION_FAILED", "无法连接模型服务，请检查地址和网络"),
        ]
        for error_code, message in cases:
            with patch(
                "api.views.test_model_config",
                side_effect=ModelServiceError(message, error_code),
            ):
                response = self.client.post(f"/api/model-configs/{config.id}/test/")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["message"], message)
            self.assertNotIn("stage05-temporary-secret-ABCD", str(response.data))

    def test_environment_and_local_fallback_status_are_preserved(self):
        knowledge = KnowledgeBase.objects.create(name="stage05-fallback", owner=self.user)
        url = f"/api/knowledge-bases/{knowledge.id}/model-config/"
        response = self.client.get(url)
        self.assertEqual(response.data["data"]["chat"]["source"], "LOCAL")
        self.assertEqual(response.data["data"]["embedding"]["source"], "LOCAL")

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "stage05-env-key",
                "LLM_MODEL": "env-chat",
                "EMBEDDING_MODEL": "env-embedding",
            },
        ):
            response = self.client.get(url)
        self.assertEqual(response.data["data"]["chat"]["source"], "ENVIRONMENT")
        self.assertEqual(response.data["data"]["embedding"]["source"], "ENVIRONMENT")
        self.assertNotIn("stage05-env-key", str(response.data))

    def test_embedding_signature_marks_stale_excludes_search_and_updates_after_reprocess(self):
        embedding, _ = self.create_config(
            "stage05-signature-embedding", ModelConfig.ModelType.EMBEDDING
        )
        knowledge = KnowledgeBase.objects.create(
            name="stage05-signature",
            owner=self.user,
            embedding_model_config=embedding,
        )
        document = Document.objects.create(
            knowledge_base=knowledge,
            name="stage05-signature.txt",
            file=SimpleUploadedFile("stage05-signature.txt", "向量版本测试".encode("utf-8")),
        )
        from .services.document_processor import process_document
        from .services.rag import search_paragraphs

        with patch("api.services.document_processor.embed_texts", return_value=[[0.5, 0.5]]):
            process_document(document)
        document.refresh_from_db()
        self.assertEqual(document.embedding_signature, f"config:{embedding.id}:revision:1")

        embedding.revision = 2
        embedding.save(update_fields=["revision"])
        response = self.client.get(f"/api/knowledge-bases/{knowledge.id}/documents/")
        self.assertTrue(response.data["data"][0]["needs_reprocess"])
        with patch("api.services.rag.embed_texts", return_value=[[0.5, 0.5]]):
            self.assertEqual(search_paragraphs(knowledge, "测试"), [])

        old_paragraph_ids = list(document.paragraphs.values_list("id", flat=True))
        old_signature = document.embedding_signature
        with patch(
            "api.services.document_processor.embed_texts",
            side_effect=RuntimeError("stage05 reprocess failed"),
        ):
            with self.assertRaises(RuntimeError):
                process_document(document)
        document.refresh_from_db()
        self.assertEqual(document.embedding_signature, old_signature)
        self.assertEqual(list(document.paragraphs.values_list("id", flat=True)), old_paragraph_ids)

        with patch("api.services.document_processor.embed_texts", return_value=[[0.2, 0.8]]):
            process_document(document)
        document.refresh_from_db()
        self.assertEqual(document.embedding_signature, f"config:{embedding.id}:revision:2")
        self.assertFalse(document.error_message)
        with patch("api.services.rag.embed_texts", return_value=[[0.2, 0.8]]):
            self.assertTrue(search_paragraphs(knowledge, "测试"))

    def test_search_failure_returns_created_conversation_and_keeps_only_user_message(self):
        knowledge = KnowledgeBase.objects.create(name="stage04-检索失败", owner=self.user)

        with patch(
            "api.views.search_paragraphs",
            side_effect=ModelServiceError("向量服务暂时不可用", "network_error"),
        ):
            response = self.client.post(
                f"/api/knowledge-bases/{knowledge.id}/chat/stream/",
                {"message": "检索失败测试", "conversation_id": None},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("向量服务暂时不可用", response.data["message"])
        conversation = Conversation.objects.get(pk=response.data["data"]["conversation_id"])
        self.assertEqual(conversation.messages.count(), 1)
        self.assertEqual(conversation.messages.first().role, Message.Role.USER)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StageSixRetrievalTests(APITestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "LLM_MODEL": "", "EMBEDDING_MODEL": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.user = User.objects.create_user(username="stage06-user", password="test-password")
        self.other_user = User.objects.create_user(
            username="stage06-other",
            password="test-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_document(self, knowledge, name="stage06.txt", status=Document.Status.SUCCESS):
        return Document.objects.create(
            knowledge_base=knowledge,
            name=name,
            file=SimpleUploadedFile(name, b"stage06 temporary content"),
            status=status,
        )

    def add_paragraph(self, document, position, content, embedding):
        paragraph = Paragraph.objects.create(
            document=document,
            position=position,
            content=content,
            embedding=embedding,
        )
        document.paragraph_count = document.paragraphs.count()
        document.save(update_fields=["paragraph_count"])
        return paragraph

    def stream_chat(self, knowledge, message):
        response = self.client.post(
            f"/api/knowledge-bases/{knowledge.id}/chat/stream/",
            {"message": message},
            format="json",
        )
        body = b"".join(response.streaming_content).decode("utf-8")
        return response, body

    def test_retrieval_config_defaults_update_owner_and_validation(self):
        knowledge = KnowledgeBase.objects.create(name="stage06-config", owner=self.user)
        url = f"/api/knowledge-bases/{knowledge.id}/retrieval-config/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["data"],
            {
                "retrieval_mode": "VECTOR",
                "retrieval_top_k": 5,
                "similarity_threshold": 0.0,
                "vector_weight": 1.0,
                "fusion_method": "WEIGHTED",
                "vector_candidate_k": 30,
                "keyword_candidate_k": 30,
                "rrf_k": 60,
                "rerank_enabled": False,
                "rerank_candidate_k": 20,
                "max_context_chars": 6000,
                "system_prompt": "",
                "no_answer_message": "",
            },
        )

        response = self.client.patch(
            url,
            {
                "retrieval_mode": "HYBRID",
                "retrieval_top_k": 8,
                "similarity_threshold": 0.35,
                "vector_weight": 0.7,
                "max_context_chars": 9000,
                "system_prompt": "  请简洁回答  ",
                "no_answer_message": "  暂无可靠资料  ",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        knowledge.refresh_from_db()
        self.assertEqual(knowledge.retrieval_mode, "HYBRID")
        self.assertEqual(knowledge.system_prompt, "请简洁回答")
        self.assertEqual(knowledge.no_answer_message, "暂无可靠资料")

        invalid_payloads = [
            {"retrieval_top_k": 0},
            {"retrieval_top_k": 21},
            {"similarity_threshold": -0.01},
            {"similarity_threshold": 1.01},
            {"vector_weight": -0.01},
            {"vector_weight": 1.01},
            {"max_context_chars": 999},
            {"max_context_chars": 30001},
            {"system_prompt": "x" * 2001},
            {"no_answer_message": "x" * 501},
            {"retrieval_top_k": None},
        ]
        for payload in invalid_payloads:
            self.assertEqual(self.client.patch(url, payload, format="json").status_code, 400)

        other_knowledge = KnowledgeBase.objects.create(name="stage06-private", owner=self.other_user)
        other_url = f"/api/knowledge-bases/{other_knowledge.id}/retrieval-config/"
        self.assertEqual(self.client.get(other_url).status_code, 404)
        self.assertEqual(
            self.client.patch(other_url, {"retrieval_top_k": 2}, format="json").status_code,
            404,
        )

    def test_tokenizer_bm25_cosine_and_normalization_formulas(self):
        tokens = tokenize_text("Python 3 与知识库RAG")
        self.assertIn("python", tokens)
        self.assertIn("3", tokens)
        self.assertIn("知", tokens)
        self.assertIn("知识", tokens)

        scores = calculate_bm25_scores(
            ["apple"],
            {1: ["apple"], 2: ["banana"]},
        )
        self.assertAlmostEqual(scores[1].raw, math.log(2), places=6)
        self.assertEqual(scores[1].normalized, 1)
        self.assertEqual(scores[2].normalized, 0)
        self.assertEqual(calculate_bm25_scores([], {1: ["apple"]})[1].normalized, 0)

        self.assertAlmostEqual(cosine_similarity([2, 0], [3, 0]), 1)
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1)
        self.assertEqual(cosine_similarity([0, 0], [1, 0]), 0)
        self.assertEqual(normalize_vector_score(-1), 0)
        self.assertEqual(normalize_vector_score(0), 0.5)
        self.assertEqual(normalize_vector_score(1), 1)

    def test_hybrid_formula_stable_sort_and_default_vector_compatibility(self):
        knowledge = KnowledgeBase.objects.create(
            name="stage06-hybrid",
            owner=self.user,
            retrieval_mode=KnowledgeBase.RetrievalMode.HYBRID,
            vector_weight=0.5,
            retrieval_top_k=20,
        )
        document = self.create_document(knowledge)
        first = self.add_paragraph(document, 1, "apple", [1.0, 0.0])
        second = self.add_paragraph(document, 2, "apple", [1.0, 0.0])
        third = self.add_paragraph(document, 3, "banana", [0.0, 1.0])

        result = retrieve_candidates(
            knowledge,
            "apple",
            embedding_function=lambda texts, kb: [[1.0, 0.0]],
        )
        self.assertEqual([item.paragraph_id for item in result.candidates[:2]], [first.id, second.id])
        self.assertAlmostEqual(result.candidates[0].final_score, 1.0)
        third_item = next(item for item in result.candidates if item.paragraph_id == third.id)
        self.assertAlmostEqual(third_item.final_score, 0.25)

        knowledge.retrieval_mode = KnowledgeBase.RetrievalMode.VECTOR
        knowledge.vector_weight = 1
        knowledge.save(update_fields=["retrieval_mode", "vector_weight"])
        result = retrieve_candidates(
            knowledge,
            "apple",
            embedding_function=lambda texts, kb: [[1.0, 0.0]],
        )
        self.assertEqual(result.settings.top_k, 20)
        self.assertEqual(result.candidates[0].final_score, result.candidates[0].vector_score_normalized)

    def test_threshold_top_k_context_budget_and_oversized_first_paragraph(self):
        knowledge = KnowledgeBase.objects.create(
            name="stage06-budget",
            owner=self.user,
            retrieval_top_k=2,
            similarity_threshold=0.6,
            max_context_chars=1000,
        )
        document = self.create_document(knowledge)
        first = self.add_paragraph(document, 1, "A" * 1500, [1.0, 0.0])
        second = self.add_paragraph(document, 2, "B" * 100, [0.8, 0.6])
        third = self.add_paragraph(document, 3, "C" * 100, [0.0, 1.0])

        result = retrieve_candidates(
            knowledge,
            "budget",
            embedding_function=lambda texts, kb: [[1.0, 0.0]],
        )
        first_item = next(item for item in result.candidates if item.paragraph_id == first.id)
        second_item = next(item for item in result.candidates if item.paragraph_id == second.id)
        third_item = next(item for item in result.candidates if item.paragraph_id == third.id)
        self.assertTrue(first_item.included)
        self.assertTrue(first_item.content_truncated)
        self.assertLessEqual(result.context_chars, 1000)
        self.assertFalse(second_item.included)
        self.assertEqual(second_item.exclusion_reason, "超过上下文字符预算")
        self.assertFalse(third_item.included)
        self.assertEqual(third_item.exclusion_reason, "低于相关度阈值")

        knowledge.similarity_threshold = 0
        knowledge.max_context_chars = 30000
        knowledge.retrieval_top_k = 1
        knowledge.save(
            update_fields=["similarity_threshold", "max_context_chars", "retrieval_top_k"]
        )
        result = retrieve_candidates(
            knowledge,
            "budget",
            embedding_function=lambda texts, kb: [[1.0, 0.0]],
        )
        self.assertEqual(result.selected_count, 1)
        self.assertTrue(result.candidates[0].included)
        self.assertTrue(
            all(item.exclusion_reason == "超过返回数量限制" for item in result.candidates[1:])
        )

    def test_signature_and_failure_status_are_excluded_before_scoring(self):
        knowledge = KnowledgeBase.objects.create(name="stage06-filter", owner=self.user)
        successful = self.create_document(knowledge, "success.txt")
        self.add_paragraph(successful, 1, "可用", [1.0, 0.0])
        failed = self.create_document(knowledge, "failed.txt", Document.Status.FAILURE)
        self.add_paragraph(failed, 1, "失败文档", [1.0, 0.0])

        result = retrieve_candidates(
            knowledge,
            "可用",
            embedding_function=lambda texts, kb: [[1.0, 0.0]],
        )
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.candidates[0].document_name, "success.txt")

        embedding_config = ModelConfig.objects.create(
            owner=self.user,
            name="stage06-embedding",
            model_type=ModelConfig.ModelType.EMBEDDING,
            base_url="https://example.com/v1",
            model_name="embedding",
            encrypted_api_key="unused",
            revision=2,
        )
        knowledge.embedding_model_config = embedding_config
        knowledge.save(update_fields=["embedding_model_config"])
        result = retrieve_candidates(
            knowledge,
            "可用",
            embedding_function=lambda texts, kb: [[1.0, 0.0]],
        )
        self.assertEqual(result.candidate_count, 0)

    def test_debug_api_scores_without_embedding_chat_or_persistence(self):
        knowledge = KnowledgeBase.objects.create(
            name="stage06-debug",
            owner=self.user,
            retrieval_mode=KnowledgeBase.RetrievalMode.HYBRID,
            vector_weight=0.7,
        )
        document = self.create_document(knowledge)
        self.add_paragraph(document, 1, "Embedding 配置改变后需要重新处理", [1.0, 0.0])

        with patch("api.services.retrieval.embed_texts", return_value=[[1.0, 0.0]]), patch(
            "api.services.rag.create_openai_client"
        ) as chat_client:
            response = self.client.post(
                f"/api/knowledge-bases/{knowledge.id}/retrieval/debug/",
                {"query": "为什么要重新处理", "candidate_limit": 20},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        item = response.data["data"]["items"][0]
        self.assertIn("vector_score_raw", item)
        self.assertIn("vector_score_normalized", item)
        self.assertIn("keyword_score", item)
        self.assertIn("final_score", item)
        self.assertNotIn("embedding", json.dumps(response.data, ensure_ascii=False))
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())
        chat_client.assert_not_called()

        self.assertEqual(
            self.client.post(
                f"/api/knowledge-bases/{knowledge.id}/retrieval/debug/",
                {"query": "   "},
                format="json",
            ).status_code,
            400,
        )
        private = KnowledgeBase.objects.create(name="stage06-debug-private", owner=self.other_user)
        self.assertEqual(
            self.client.post(
                f"/api/knowledge-bases/{private.id}/retrieval/debug/",
                {"query": "越权"},
                format="json",
            ).status_code,
            404,
        )

    def test_no_results_skip_chat_and_persist_custom_answer_without_references(self):
        knowledge = KnowledgeBase.objects.create(
            name="stage06-no-answer",
            owner=self.user,
            similarity_threshold=0.9,
            no_answer_message="当前资料不足以回答这个问题。",
        )
        document = self.create_document(knowledge)
        self.add_paragraph(document, 1, "无关资料", [0.0, 1.0])

        with patch("api.services.rag.embed_texts", return_value=[[1.0, 0.0]]), patch(
            "api.services.rag.resolve_chat_config"
        ) as resolver:
            response, body = self.stream_chat(knowledge, "完全不相关的问题")

        self.assertEqual(response.status_code, 200)
        self.assertIn("当前资料不足以回答这个问题", body)
        self.assertIn("event: done", body)
        resolver.assert_not_called()
        conversation = Conversation.objects.get(knowledge_base=knowledge)
        messages = list(conversation.messages.order_by("created_at", "id"))
        self.assertEqual([item.role for item in messages], ["user", "assistant"])
        self.assertEqual(messages[1].content, "当前资料不足以回答这个问题。")
        self.assertEqual(messages[1].references, [])

    def test_custom_system_prompt_sse_and_scored_references_are_persisted_in_order(self):
        knowledge = KnowledgeBase.objects.create(
            name="stage06-prompt",
            owner=self.user,
            system_prompt="这是 stage06 自定义系统提示。",
            retrieval_top_k=2,
        )
        document = self.create_document(knowledge)
        first = self.add_paragraph(document, 1, "第一条相关资料", [1.0, 0.0])
        second = self.add_paragraph(document, 2, "第二条相关资料", [0.8, 0.6])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="模型回答"))])
        ]
        context = MagicMock()
        context.__enter__.return_value = mock_client
        context.__exit__.return_value = False
        resolved = SimpleNamespace(model_name="stage06-chat")

        with patch("api.services.rag.embed_texts", return_value=[[1.0, 0.0]]), patch(
            "api.services.rag.resolve_chat_config", return_value=resolved
        ), patch("api.services.rag.create_openai_client", return_value=context):
            response, body = self.stream_chat(knowledge, "测试自定义提示")

        self.assertEqual(response.status_code, 200)
        self.assertIn("模型回答", body)
        call = mock_client.chat.completions.create.call_args
        self.assertIn("这是 stage06 自定义系统提示。", call.kwargs["messages"][0]["content"])
        self.assertIn("不可信数据", call.kwargs["messages"][0]["content"])
        self.assertIn("<reference", call.kwargs["messages"][1]["content"])
        assistant = Message.objects.get(conversation__knowledge_base=knowledge, role="assistant")
        self.assertEqual(
            [item["paragraph_id"] for item in assistant.references],
            [first.id, second.id],
        )
        self.assertTrue(all("final_score" in item for item in assistant.references))
        self.assertNotIn("embedding", json.dumps(assistant.references, ensure_ascii=False))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StageSevenAgentTests(APITestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "", "LLM_MODEL": "", "EMBEDDING_MODEL": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.user = User.objects.create_user(username="stage07-user", password="test-password")
        self.other_user = User.objects.create_user(
            username="stage07-other",
            password="test-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_agent_context(self, **knowledge_fields):
        fields = {
            "name": "stage07-agent",
            "owner": self.user,
            "agent_enabled": True,
            "enabled_tools": ["knowledge_search", "document_list", "calculator"],
        }
        fields.update(knowledge_fields)
        knowledge = KnowledgeBase.objects.create(**fields)
        conversation = Conversation.objects.create(
            knowledge_base=knowledge,
            owner=self.user,
            title="stage07-conversation",
        )
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content="请执行Agent任务",
        )
        agent_run = create_agent_run(conversation, user_message)
        return knowledge, conversation, user_message, agent_run

    def content_chunk(self, content, *, reasoning_content=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=content,
                        tool_calls=None,
                        reasoning_content=reasoning_content,
                    )
                )
            ]
        )

    def tool_chunk(self, index, *, call_id=None, name=None, arguments=None):
        function = SimpleNamespace(name=name, arguments=arguments)
        tool_call = SimpleNamespace(index=index, id=call_id, function=function, type="function")
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tool_call]))]
        )

    def mock_client_context(self, turns):
        client = MagicMock()
        client.chat.completions.create.side_effect = turns
        context = MagicMock()
        context.__enter__.return_value = client
        context.__exit__.return_value = False
        return context, client

    def run_with_turns(self, agent_run, turns):
        context, client = self.mock_client_context(turns)
        resolved = SimpleNamespace(model_name="stage07-chat")
        with patch("api.services.agent_executor.resolve_chat_config", return_value=resolved), patch(
            "api.services.agent_executor.create_openai_client", return_value=context
        ):
            events = list(stream_agent_run(agent_run))
        agent_run.refresh_from_db()
        return events, client

    def test_agent_config_defaults_update_validation_owner_and_tool_order(self):
        knowledge = KnowledgeBase.objects.create(name="stage07-config", owner=self.user)
        url = f"/api/knowledge-bases/{knowledge.id}/agent-config/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["data"]["agent_enabled"])
        self.assertEqual(response.data["data"]["agent_max_steps"], 5)
        self.assertEqual(response.data["data"]["enabled_tools"], [])
        self.assertEqual(len(response.data["data"]["available_tools"]), 3)
        self.assertFalse(response.data["data"]["chat_model_status"]["available"])

        response = self.client.patch(
            url,
            {
                "agent_enabled": True,
                "agent_max_steps": 7,
                "agent_system_prompt": "  先使用工具  ",
                "enabled_tools": ["calculator", "knowledge_search", "calculator"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["agent_system_prompt"], "先使用工具")
        self.assertEqual(
            response.data["data"]["enabled_tools"],
            ["knowledge_search", "calculator"],
        )

        for payload in [
            {"agent_max_steps": 0},
            {"agent_max_steps": 11},
            {"agent_system_prompt": "x" * 2001},
            {"enabled_tools": ["shell"]},
            {"enabled_tools": "calculator"},
        ]:
            self.assertEqual(self.client.patch(url, payload, format="json").status_code, 400)

        other_knowledge = KnowledgeBase.objects.create(name="stage07-private", owner=self.other_user)
        other_url = f"/api/knowledge-bases/{other_knowledge.id}/agent-config/"
        self.assertEqual(self.client.get(other_url).status_code, 404)
        self.assertEqual(
            self.client.patch(other_url, {"agent_enabled": True}, format="json").status_code,
            404,
        )

    def test_calculator_allows_arithmetic_and_rejects_code_and_resource_abuse(self):
        self.assertEqual(calculate("(18000 * 0.027 + 18000) / 3"), 6162)
        self.assertEqual(calculate("10 // 3 + 10 % 3"), 4)
        for expression in [
            "__import__('os').system('whoami')",
            "(1).__class__",
            "secret + 1",
            "[x for x in [1]]",
            "2 ** 1000",
            "1 / 0",
            "'text'",
        ]:
            with self.assertRaises(ToolRejectedError, msg=expression):
                calculate(expression)

    def test_document_list_is_context_scoped_and_has_safe_fields(self):
        knowledge, conversation, _, agent_run = self.create_agent_context()
        Document.objects.create(
            knowledge_base=knowledge,
            name="stage07-own.txt",
            file=SimpleUploadedFile("stage07-own.txt", b"own"),
            status=Document.Status.SUCCESS,
            paragraph_count=2,
        )
        other_knowledge = KnowledgeBase.objects.create(name="stage07-other-kb", owner=self.other_user)
        Document.objects.create(
            knowledge_base=other_knowledge,
            name="stage07-secret.txt",
            file=SimpleUploadedFile("stage07-secret.txt", b"secret"),
        )
        definition = get_tool("document_list")
        result = definition.handler(
            ToolContext(self.user, knowledge, conversation, agent_run),
            definition.validate_arguments({}),
        )
        serialized = json.dumps(result.payload, ensure_ascii=False)
        self.assertIn("stage07-own.txt", serialized)
        self.assertNotIn("stage07-secret.txt", serialized)
        self.assertNotIn("file", result.payload["items"][0])
        self.assertNotIn("embedding_signature", serialized)

    def test_knowledge_search_reuses_retrieval_and_does_not_expose_embedding(self):
        knowledge, conversation, _, agent_run = self.create_agent_context(retrieval_top_k=3)
        definition = get_tool("knowledge_search")
        fake_result = MagicMock()
        fake_result.references.return_value = [
            {
                "paragraph_id": 1,
                "document_id": 2,
                "document_name": "stage07.txt",
                "position": 1,
                "content": "安全正文",
                "similarity": 0.9,
            }
        ]
        with patch(
            "api.services.agent_tools.knowledge_search.retrieve_candidates",
            return_value=fake_result,
        ) as retrieve:
            result = definition.handler(
                ToolContext(self.user, knowledge, conversation, agent_run),
                definition.validate_arguments({"query": "测试", "top_k": 10}),
            )
        self.assertEqual(retrieve.call_args.kwargs["top_k_override"], 3)
        self.assertNotIn("embedding", json.dumps(result.payload, ensure_ascii=False))
        self.assertEqual(result.references[0]["paragraph_id"], 1)
        with self.assertRaises(ToolRejectedError):
            definition.validate_arguments({"query": "test", "knowledge_id": 999})

    def test_agent_direct_answer_succeeds_without_tools_or_reasoning_leak(self):
        _, conversation, _, agent_run = self.create_agent_context(enabled_tools=[])
        events, client = self.run_with_turns(
            agent_run,
            [[self.content_chunk("最终回答", reasoning_content="hidden-chain")]],
        )
        self.assertEqual(agent_run.status, AgentRun.Status.SUCCESS)
        assistant = conversation.messages.get(role=Message.Role.ASSISTANT)
        self.assertEqual(assistant.content, "最终回答")
        body = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("hidden-chain", body)
        self.assertIn("content", [event["event"] for event in events])
        self.assertNotIn("tools", client.chat.completions.create.call_args.kwargs)

    def test_agent_executes_multiple_tools_in_stable_order_then_answers(self):
        _, conversation, _, agent_run = self.create_agent_context()
        turns = [
            [
                self.tool_chunk(0, call_id="call-", name="doc", arguments="{"),
                self.tool_chunk(1, call_id="call-2", name="calculator", arguments='{"expression":"6*7"}'),
                self.tool_chunk(0, call_id="1", name="ument_list", arguments="}"),
            ],
            [self.content_chunk("共有文档，计算结果是42。")],
        ]
        events, client = self.run_with_turns(agent_run, turns)
        executions = list(agent_run.tool_executions.order_by("step", "sequence"))
        self.assertEqual([item.tool_name for item in executions], ["document_list", "calculator"])
        self.assertTrue(all(item.status == ToolExecution.Status.SUCCESS for item in executions))
        names = [event["event"] for event in events]
        self.assertLess(names.index("tool_start"), names.index("tool_result"))
        self.assertEqual(agent_run.status, AgentRun.Status.SUCCESS)
        self.assertEqual(conversation.messages.filter(role=Message.Role.ASSISTANT).count(), 1)
        second_messages = client.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertEqual([item["role"] for item in second_messages[-3:]], ["assistant", "tool", "tool"])

    def test_agent_search_references_are_deduplicated_and_persisted(self):
        _, conversation, _, agent_run = self.create_agent_context(enabled_tools=["knowledge_search"])
        turns = [
            [self.tool_chunk(0, call_id="search-1", name="knowledge_search", arguments='{"query":"RAG"}')],
            [self.content_chunk("RAG 是检索增强生成。[资料1]")],
        ]
        fake_result = MagicMock()
        fake_result.references.return_value = [
            {
                "paragraph_id": 7,
                "document_id": 4,
                "document_name": "rag.txt",
                "position": 1,
                "content": "RAG 是检索增强生成",
                "similarity": 0.95,
            },
            {
                "paragraph_id": 7,
                "document_id": 4,
                "document_name": "rag.txt",
                "position": 1,
                "content": "RAG 是检索增强生成",
                "similarity": 0.95,
            },
        ]
        with patch(
            "api.services.agent_tools.knowledge_search.retrieve_candidates",
            return_value=fake_result,
        ):
            self.run_with_turns(agent_run, turns)
        assistant = conversation.messages.get(role=Message.Role.ASSISTANT)
        self.assertEqual(len(assistant.references), 1)
        self.assertEqual(assistant.references[0]["paragraph_id"], 7)

    def test_invalid_unknown_and_disabled_tools_are_rejected_and_audited(self):
        cases = [
            ("unknown_tool", "{}", "TOOL_NOT_FOUND"),
            ("calculator", "{}", "TOOL_ARGUMENTS_INVALID"),
        ]
        for index, (name, arguments, error_code) in enumerate(cases):
            _, conversation, _, agent_run = self.create_agent_context(name=f"stage07-reject-{index}")
            if name == "calculator" and index == 1:
                agent_run.conversation.knowledge_base.enabled_tools = []
                agent_run.conversation.knowledge_base.save(update_fields=["enabled_tools"])
                error_code = "TOOL_NOT_ALLOWED"
            events, _ = self.run_with_turns(
                agent_run,
                [[self.tool_chunk(0, call_id=f"reject-{index}", name=name, arguments=arguments)]],
            )
            execution = agent_run.tool_executions.get()
            self.assertEqual(execution.status, ToolExecution.Status.REJECTED)
            self.assertEqual(execution.error_code, error_code)
            self.assertEqual(agent_run.status, AgentRun.Status.FAILURE)
            self.assertFalse(conversation.messages.filter(role=Message.Role.ASSISTANT).exists())
            self.assertIn("tool_error", [event["event"] for event in events])

        _, invalid_conversation, _, invalid_run = self.create_agent_context(
            name="stage07-invalid-arguments",
            enabled_tools=["calculator"],
        )
        self.run_with_turns(
            invalid_run,
            [[self.tool_chunk(0, call_id="invalid-json", name="calculator", arguments="{")]],
        )
        invalid_execution = invalid_run.tool_executions.get()
        self.assertEqual(invalid_execution.error_code, "TOOL_ARGUMENTS_INVALID")
        self.assertEqual(invalid_execution.arguments, {})
        self.assertFalse(
            invalid_conversation.messages.filter(role=Message.Role.ASSISTANT).exists()
        )

    def test_oversized_tool_result_is_stopped_and_audited_without_assistant(self):
        _, conversation, _, agent_run = self.create_agent_context(
            name="stage07-large-result",
            enabled_tools=["calculator"],
        )
        oversized = ToolDefinition(
            name="calculator",
            label="测试工具",
            description="测试结果限制",
            input_schema={"type": "object", "properties": {}},
            validate_arguments=lambda arguments: {},
            handler=lambda context, arguments: ToolResult(
                summary="超长结果",
                payload={"content": "x" * 7000},
                model_payload={"content": "x" * 7000},
            ),
        )
        context, _ = self.mock_client_context(
            [[self.tool_chunk(0, call_id="large", name="calculator", arguments="{}")]]
        )
        with patch(
            "api.services.agent_executor.resolve_chat_config",
            return_value=SimpleNamespace(model_name="stage07-chat"),
        ), patch("api.services.agent_executor.create_openai_client", return_value=context), patch(
            "api.services.agent_executor.get_tool", return_value=oversized
        ):
            events = list(stream_agent_run(agent_run))
        agent_run.refresh_from_db()
        execution = agent_run.tool_executions.get()
        self.assertEqual(execution.status, ToolExecution.Status.FAILURE)
        self.assertEqual(execution.error_code, "TOOL_RESULT_TOO_LARGE")
        self.assertEqual(agent_run.error_code, "TOOL_RESULT_TOO_LARGE")
        self.assertFalse(conversation.messages.filter(role=Message.Role.ASSISTANT).exists())
        self.assertNotIn("x" * 100, json.dumps(events, ensure_ascii=False))

    def test_step_limit_and_tool_call_limit_do_not_save_fake_answer(self):
        _, conversation, _, agent_run = self.create_agent_context(agent_max_steps=1)
        events, _ = self.run_with_turns(
            agent_run,
            [[self.tool_chunk(0, call_id="limit", name="calculator", arguments='{"expression":"1+1"}')]],
        )
        self.assertEqual(agent_run.status, AgentRun.Status.LIMIT_REACHED)
        self.assertEqual(agent_run.error_code, "AGENT_STEP_LIMIT")
        self.assertFalse(conversation.messages.filter(role=Message.Role.ASSISTANT).exists())
        self.assertFalse(agent_run.tool_executions.exists())
        self.assertIn("agent_done", [event["event"] for event in events])

        _, _, _, too_many_run = self.create_agent_context(name="stage07-too-many")
        calls = [
            self.tool_chunk(index, call_id=f"many-{index}", name="calculator", arguments='{"expression":"1+1"}')
            for index in range(4)
        ]
        self.run_with_turns(too_many_run, [calls])
        self.assertEqual(too_many_run.error_code, "TOOL_CALL_LIMIT")
        self.assertFalse(too_many_run.tool_executions.exists())

    def test_missing_model_unsupported_tools_and_cancel_have_safe_terminal_states(self):
        _, _, _, missing_run = self.create_agent_context(name="stage07-no-model")
        events = list(stream_agent_run(missing_run))
        missing_run.refresh_from_db()
        self.assertEqual(missing_run.error_code, "AGENT_MODEL_REQUIRED")
        self.assertNotIn("API Key", json.dumps(events, ensure_ascii=False))

        _, _, _, unsupported_run = self.create_agent_context(name="stage07-unsupported")
        self.run_with_turns(unsupported_run, [Exception("tools are unsupported by provider")])
        self.assertEqual(unsupported_run.error_code, "TOOL_CALLING_UNSUPPORTED")
        self.assertNotIn("provider", unsupported_run.error_message)

        _, _, _, cancelled_run = self.create_agent_context(name="stage07-cancel")
        context, _ = self.mock_client_context([[self.content_chunk("不会完成")]])
        with patch(
            "api.services.agent_executor.resolve_chat_config",
            return_value=SimpleNamespace(model_name="stage07-chat"),
        ), patch("api.services.agent_executor.create_openai_client", return_value=context):
            generator = stream_agent_run(cancelled_run)
            self.assertEqual(next(generator)["event"], "agent_start")
            generator.close()
        cancelled_run.refresh_from_db()
        self.assertEqual(cancelled_run.status, AgentRun.Status.CANCELLED)

    def test_chat_sse_emits_agent_events_and_failure_saves_only_user_message(self):
        knowledge = KnowledgeBase.objects.create(
            name="stage07-sse",
            owner=self.user,
            agent_enabled=True,
            enabled_tools=[],
        )
        context, _ = self.mock_client_context([[self.content_chunk("Agent SSE回答")]])
        with patch(
            "api.services.agent_executor.resolve_chat_config",
            return_value=SimpleNamespace(model_name="stage07-chat"),
        ), patch("api.services.agent_executor.create_openai_client", return_value=context):
            response = self.client.post(
                f"/api/knowledge-bases/{knowledge.id}/chat/stream/",
                {"message": "测试Agent SSE"},
                format="json",
            )
            body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("event: meta", body)
        self.assertIn("event: agent_start", body)
        self.assertIn("event: agent_step", body)
        self.assertIn("event: content", body)
        self.assertIn("event: agent_done", body)
        self.assertIn("event: done", body)
        self.assertNotIn("reasoning_content", body)

        failing = KnowledgeBase.objects.create(
            name="stage07-sse-failure",
            owner=self.user,
            agent_enabled=True,
            enabled_tools=[],
        )
        events = [Exception("internal model path C:/secret and key=stage07-secret")]
        context, _ = self.mock_client_context(events)
        with patch(
            "api.services.agent_executor.resolve_chat_config",
            return_value=SimpleNamespace(model_name="stage07-chat"),
        ), patch("api.services.agent_executor.create_openai_client", return_value=context):
            response = self.client.post(
                f"/api/knowledge-bases/{failing.id}/chat/stream/",
                {"message": "失败测试"},
                format="json",
            )
            failure_body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("event: error", failure_body)
        self.assertNotIn("C:/secret", failure_body)
        self.assertNotIn("stage07-secret", failure_body)
        conversation = Conversation.objects.get(knowledge_base=failing)
        self.assertEqual(conversation.messages.count(), 1)

    def test_history_restores_trace_without_cross_owner_access_and_cascades(self):
        knowledge, conversation, user_message, agent_run = self.create_agent_context()
        assistant = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="完成",
        )
        agent_run.status = AgentRun.Status.SUCCESS
        agent_run.assistant_message = assistant
        agent_run.finished_at = timezone.now()
        agent_run.save(update_fields=["status", "assistant_message", "finished_at"])
        ToolExecution.objects.create(
            agent_run=agent_run,
            step=1,
            sequence=1,
            tool_call_id="history-call",
            tool_name="calculator",
            arguments={"expression": "1+1"},
            result_summary="计算结果：2",
            result_payload={"result": 2},
            status=ToolExecution.Status.SUCCESS,
        )

        response = self.client.get(
            f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/messages/"
        )
        self.assertEqual(response.status_code, 200)
        items = response.data["data"]["items"]
        self.assertIsNone(next(item for item in items if item["id"] == user_message.id)["agent_trace"])
        assistant_item = next(item for item in items if item["id"] == assistant.id)
        self.assertEqual(assistant_item["agent_trace"]["id"], agent_run.id)
        self.assertEqual(assistant_item["agent_trace"]["tool_executions"][0]["tool_name"], "calculator")

        trace_url = f"/api/knowledge-bases/{knowledge.id}/agent-runs/{agent_run.id}/"
        self.assertEqual(self.client.get(trace_url).status_code, 200)
        other_client = APIClient()
        other_client.force_authenticate(self.other_user)
        self.assertEqual(other_client.get(trace_url).status_code, 404)
        wrong_knowledge = KnowledgeBase.objects.create(name="stage07-wrong", owner=self.user)
        self.assertEqual(
            self.client.get(
                f"/api/knowledge-bases/{wrong_knowledge.id}/agent-runs/{agent_run.id}/"
            ).status_code,
            404,
        )

        run_id = agent_run.id
        execution_id = agent_run.tool_executions.get().id
        conversation.delete()
        self.assertFalse(AgentRun.objects.filter(pk=run_id).exists())
        self.assertFalse(ToolExecution.objects.filter(pk=execution_id).exists())

    def test_history_trace_query_count_is_bounded_and_knowledge_delete_cascades(self):
        knowledge = KnowledgeBase.objects.create(name="stage07-query-budget", owner=self.user)
        conversation = Conversation.objects.create(
            knowledge_base=knowledge,
            owner=self.user,
            title="stage07-query-budget",
        )
        run_ids = []
        execution_ids = []
        for index in range(10):
            user_message = Message.objects.create(
                conversation=conversation,
                role=Message.Role.USER,
                content=f"问题{index}",
            )
            assistant = Message.objects.create(
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content=f"回答{index}",
            )
            agent_run = AgentRun.objects.create(
                conversation=conversation,
                user_message=user_message,
                assistant_message=assistant,
                status=AgentRun.Status.SUCCESS,
                finished_at=timezone.now(),
            )
            execution = ToolExecution.objects.create(
                agent_run=agent_run,
                step=1,
                sequence=1,
                tool_call_id=f"query-{index}",
                tool_name="calculator",
                arguments={"expression": "1+1"},
                result_summary="计算结果：2",
                status=ToolExecution.Status.SUCCESS,
            )
            run_ids.append(agent_run.id)
            execution_ids.append(execution.id)

        url = f"/api/knowledge-bases/{knowledge.id}/conversations/{conversation.id}/messages/"
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 8)
        self.assertEqual(
            sum(1 for item in response.data["data"]["items"] if item["agent_trace"]),
            10,
        )

        knowledge.delete()
        self.assertFalse(AgentRun.objects.filter(id__in=run_ids).exists())
        self.assertFalse(ToolExecution.objects.filter(id__in=execution_ids).exists())


class StageEightAsyncDocumentTests(APITransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix="stage08-media-")
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = User.objects.create_user(username="stage08-owner", password="password")
        self.other_user = User.objects.create_user(username="stage08-other", password="password")
        self.knowledge = KnowledgeBase.objects.create(name="stage08-kb", owner=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_document(self, name="stage08.txt", content="第一段。第二段。"):
        return Document.objects.create(
            knowledge_base=self.knowledge,
            name=name,
            file=SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/plain"),
        )

    def test_upload_returns_202_document_and_business_task(self):
        upload = SimpleUploadedFile(
            "stage08-upload.txt",
            "异步上传测试内容".encode("utf-8"),
            content_type="text/plain",
        )
        response = self.client.post(
            f"/api/knowledge-bases/{self.knowledge.id}/documents/",
            {"file": upload},
            HTTP_IDEMPOTENCY_KEY="stage08-upload-one",
        )
        self.assertEqual(response.status_code, 202)
        data = response.data["data"]
        self.assertIn("document", data)
        self.assertIn("task", data)
        task = DocumentProcessingTask.objects.get(pk=data["task"]["id"])
        self.assertEqual(task.status, DocumentProcessingTask.Status.SUCCESS)
        self.assertEqual(task.progress, 100)
        self.assertTrue(task.document.paragraphs.exists())
        self.assertNotIn("celery_task_id", data["task"])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_task_is_sent_after_commit_and_only_contains_integer_task_id(self):
        fake_result = SimpleNamespace(id="stage08-celery-id")
        with patch("api.tasks.process_document_task.apply_async", return_value=fake_result) as apply_async:
            document = self.create_document("stage08-commit.txt")
            from .services.document_tasks import create_processing_task, dispatch_processing_task

            with transaction.atomic():
                task, _ = create_processing_task(
                    document,
                    DocumentProcessingTask.TaskType.UPLOAD,
                )
                dispatch_processing_task(task.id)
                self.assertFalse(apply_async.called)
            apply_async.assert_called_once_with(args=[task.id])
        task.refresh_from_db()
        self.assertEqual(task.celery_task_id, "stage08-celery-id")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_rollback_does_not_send_task(self):
        fake_result = SimpleNamespace(id="must-not-be-used")
        with patch("api.tasks.process_document_task.apply_async", return_value=fake_result) as apply_async:
            from .services.document_tasks import create_processing_task, dispatch_processing_task

            try:
                with transaction.atomic():
                    document = self.create_document("stage08-rollback.txt")
                    task, _ = create_processing_task(
                        document,
                        DocumentProcessingTask.TaskType.UPLOAD,
                    )
                    dispatch_processing_task(task.id)
                    raise RuntimeError("force rollback")
            except RuntimeError:
                pass
        self.assertFalse(apply_async.called)
        self.assertFalse(Document.objects.filter(name="stage08-rollback.txt").exists())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_broker_failure_is_visible_and_retryable(self):
        upload = SimpleUploadedFile(
            "stage08-broker.txt",
            b"broker unavailable",
            content_type="text/plain",
        )
        with patch(
            "api.tasks.process_document_task.apply_async",
            side_effect=ConnectionError("redis://secret-internal:6379 unavailable"),
        ):
            response = self.client.post(
                f"/api/knowledge-bases/{self.knowledge.id}/documents/",
                {"file": upload},
            )
        self.assertEqual(response.status_code, 503)
        task = DocumentProcessingTask.objects.get(pk=response.data["data"]["task"]["id"])
        self.assertEqual(task.status, DocumentProcessingTask.Status.ENQUEUE_FAILED)
        self.assertEqual(task.document.status, Document.Status.FAILURE)
        self.assertNotIn("redis://", str(response.data))
        self.assertNotIn("secret-internal", str(response.data))

    def test_idempotency_key_does_not_create_duplicate_document_or_task(self):
        url = f"/api/knowledge-bases/{self.knowledge.id}/documents/"
        first = self.client.post(
            url,
            {"file": SimpleUploadedFile("stage08-idempotent.txt", b"same request")},
            HTTP_IDEMPOTENCY_KEY="stage08-same-request",
        )
        second = self.client.post(
            url,
            {"file": SimpleUploadedFile("stage08-idempotent.txt", b"same request")},
            HTTP_IDEMPOTENCY_KEY="stage08-same-request",
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.data["data"]["document"]["id"], second.data["data"]["document"]["id"])
        self.assertEqual(first.data["data"]["task"]["id"], second.data["data"]["task"]["id"])
        self.assertEqual(self.knowledge.documents.count(), 1)
        self.assertEqual(DocumentProcessingTask.objects.count(), 1)

    def test_repeated_worker_delivery_is_idempotent(self):
        document = self.create_document("stage08-redelivery.txt", "不会重复创建切片")
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
        )
        from .services.document_tasks import execute_document_processing_task

        self.assertEqual(execute_document_processing_task(task.id), DocumentProcessingTask.Status.SUCCESS)
        first_ids = list(document.paragraphs.values_list("id", flat=True))
        self.assertEqual(execute_document_processing_task(task.id), DocumentProcessingTask.Status.SUCCESS)
        self.assertEqual(list(document.paragraphs.values_list("id", flat=True)), first_ids)

    def test_retryable_failure_retries_at_most_three_times(self):
        document = self.create_document("stage08-retryable.txt")
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
        )
        from .tasks import process_document_task
        from celery.exceptions import Retry

        with patch(
            "api.tasks.execute_document_processing_task",
            side_effect=ModelServiceError("临时超时", "TIMEOUT"),
        ) as execute:
            process_document_task.push_request(retries=0)
            try:
                with patch.object(process_document_task, "retry", side_effect=Retry()) as retry:
                    with self.assertRaises(Retry):
                        process_document_task.run(task.id)
                retry_kwargs = retry.call_args.kwargs
            finally:
                process_document_task.pop_request()
        task.refresh_from_db()
        self.assertEqual(task.status, DocumentProcessingTask.Status.RETRYING)
        self.assertEqual(retry_kwargs["max_retries"], 3)
        self.assertGreaterEqual(retry_kwargs["countdown"], 5)
        self.assertLessEqual(retry_kwargs["countdown"], 7)

        with patch(
            "api.tasks.execute_document_processing_task",
            side_effect=ModelServiceError("临时超时", "TIMEOUT"),
        ) as final_execute:
            process_document_task.push_request(retries=3)
            try:
                result = process_document_task.run(task.id)
            finally:
                process_document_task.pop_request()
        task.refresh_from_db()
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(final_execute.call_count, 1)
        self.assertEqual(result["status"], "FAILURE")
        self.assertEqual(task.status, DocumentProcessingTask.Status.FAILURE)
        self.assertEqual(task.error_message, "临时超时")

    def test_non_retryable_failure_runs_once_and_is_sanitized(self):
        document = self.create_document("stage08-non-retryable.txt")
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
        )
        from .tasks import process_document_task

        with patch(
            "api.tasks.execute_document_processing_task",
            side_effect=RuntimeError("redis://internal secret trace"),
        ) as execute:
            process_document_task.apply(args=[task.id], throw=False)
        task.refresh_from_db()
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(task.status, DocumentProcessingTask.Status.FAILURE)
        self.assertEqual(task.error_message, "文档处理失败，请稍后重试")

    def test_progress_is_monotonic_and_bounded(self):
        document = self.create_document("stage08-progress.txt")
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
        )
        from .services.document_tasks import _update_progress

        _update_progress(task.id, DocumentProcessingTask.Stage.SPLITTING, 40)
        _update_progress(task.id, DocumentProcessingTask.Stage.READING, 10)
        _update_progress(task.id, DocumentProcessingTask.Stage.EMBEDDING, 999)
        task.refresh_from_db()
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.current_stage, DocumentProcessingTask.Stage.EMBEDDING)

    def test_cancel_pending_task_and_delete_queued_document_are_safe(self):
        document = self.create_document("stage08-cancel-delete.txt")
        file_path = Path(document.file.path)
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
        )
        cancel = self.client.post(
            f"/api/knowledge-bases/{self.knowledge.id}/processing-tasks/{task.id}/cancel/"
        )
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.data["data"]["status"], DocumentProcessingTask.Status.CANCELLED)
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILURE)
        self.assertEqual(document.error_message, "文档处理已取消")
        deleted = self.client.delete(
            f"/api/knowledge-bases/{self.knowledge.id}/documents/{document.id}/"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document.id).exists())
        self.assertFalse(DocumentProcessingTask.objects.filter(pk=task.id).exists())
        self.assertFalse(file_path.exists())

    def test_manual_retry_reuses_active_task_without_duplicate_dispatch(self):
        document = self.create_document("stage08-retry-race.txt")
        failed = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
            status=DocumentProcessingTask.Status.FAILURE,
        )
        active = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.REPROCESS,
        )
        from .services.document_tasks import retry_processing_task

        with patch("api.services.document_tasks.dispatch_processing_task") as dispatch:
            result = retry_processing_task(failed)
        self.assertEqual(result.id, active.id)
        self.assertFalse(dispatch.called)

    def test_reprocess_failure_keeps_old_paragraphs_and_signature(self):
        document = self.create_document("stage08-safe-reprocess.txt", "仍然可用的旧内容")
        Paragraph.objects.create(document=document, position=1, content="旧切片", embedding=[1.0])
        document.status = Document.Status.SUCCESS
        document.paragraph_count = 1
        document.embedding_signature = "legacy-safe-signature"
        document.save(update_fields=["status", "paragraph_count", "embedding_signature"])
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.REPROCESS,
        )
        from .services.document_tasks import execute_document_processing_task, mark_task_failed

        old_ids = list(document.paragraphs.values_list("id", flat=True))
        with patch(
            "api.services.document_processor.embed_texts",
            side_effect=RuntimeError("internal endpoint and key must not leak"),
        ):
            try:
                execute_document_processing_task(task.id)
            except RuntimeError as exc:
                mark_task_failed(task.id, exc)
        document.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(list(document.paragraphs.values_list("id", flat=True)), old_ids)
        self.assertEqual(document.embedding_signature, "legacy-safe-signature")
        self.assertEqual(document.status, Document.Status.SUCCESS)
        self.assertEqual(task.status, DocumentProcessingTask.Status.FAILURE)
        self.assertNotIn("internal endpoint", task.error_message)

    def test_task_resources_are_owner_and_knowledge_scoped(self):
        document = self.create_document("stage08-permission.txt")
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
            status=DocumentProcessingTask.Status.CANCELLED,
        )
        other_knowledge = KnowledgeBase.objects.create(name="stage08-other-kb", owner=self.other_user)
        other_client = APIClient()
        other_client.force_authenticate(self.other_user)
        endpoints = [
            f"/api/knowledge-bases/{self.knowledge.id}/processing-tasks/{task.id}/",
            f"/api/knowledge-bases/{self.knowledge.id}/processing-tasks/{task.id}/retry/",
            f"/api/knowledge-bases/{self.knowledge.id}/processing-tasks/{task.id}/cancel/",
        ]
        self.assertEqual(other_client.get(endpoints[0]).status_code, 404)
        self.assertEqual(other_client.post(endpoints[1]).status_code, 404)
        self.assertEqual(other_client.post(endpoints[2]).status_code, 404)
        self.assertEqual(
            self.client.get(
                f"/api/knowledge-bases/{other_knowledge.id}/processing-tasks/{task.id}/"
            ).status_code,
            404,
        )

    def test_task_list_query_count_is_bounded_and_hides_internal_fields(self):
        for index in range(10):
            document = self.create_document(f"stage08-list-{index}.txt")
            DocumentProcessingTask.objects.create(
                document=document,
                task_type=DocumentProcessingTask.TaskType.UPLOAD,
                status=DocumentProcessingTask.Status.CANCELLED,
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                f"/api/knowledge-bases/{self.knowledge.id}/processing-tasks/?page_size=100"
            )
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 5)
        serialized = str(response.data)
        self.assertNotIn("celery_task_id", serialized)
        self.assertNotIn("redis://", serialized)

    @override_settings(DOCUMENT_TASK_LOCK_BACKEND="memory")
    def test_document_lock_rejects_second_holder_and_can_be_reacquired(self):
        from .services.document_task_lock import DocumentTaskLockBusy, document_processing_lock

        document = self.create_document("stage08-lock.txt")
        with document_processing_lock(document.id):
            with self.assertRaises(DocumentTaskLockBusy):
                with document_processing_lock(document.id):
                    pass
        with document_processing_lock(document.id):
            pass


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), RERANKER_MODEL_PATH="")
class StageNineRetrievalTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="stage09-user", password="test-password")
        self.other_user = User.objects.create_user(username="stage09-other", password="test-password")
        self.client.force_authenticate(self.user)
        self.knowledge = KnowledgeBase.objects.create(
            name="stage09-kb",
            owner=self.user,
            retrieval_mode=KnowledgeBase.RetrievalMode.HYBRID,
            fusion_method=KnowledgeBase.FusionMethod.RRF,
            vector_candidate_k=3,
            keyword_candidate_k=3,
            rrf_k=60,
            retrieval_top_k=2,
            max_context_chars=3000,
        )
        self.document = Document.objects.create(
            knowledge_base=self.knowledge,
            name="stage09.txt",
            file=SimpleUploadedFile("stage09.txt", b"temporary"),
            status=Document.Status.SUCCESS,
        )

    def add_paragraph(self, position, content, embedding):
        paragraph = Paragraph.objects.create(
            document=self.document,
            position=position,
            content=content,
            embedding=embedding,
        )
        self.document.paragraph_count = self.document.paragraphs.count()
        self.document.save(update_fields=["paragraph_count"])
        return paragraph

    def test_rrf_formula_dedup_normalization_and_stable_rank(self):
        scores = reciprocal_rank_fusion({1: 1, 2: 2}, {2: 1, 3: 2}, rrf_k=60)
        self.assertEqual(set(scores), {1, 2, 3})
        self.assertEqual(scores[2].rank, 1)
        self.assertAlmostEqual(scores[2].raw, 1 / 62 + 1 / 61)
        self.assertEqual(scores[2].normalized, 1)
        self.assertEqual(scores[1].rank, 2)
        self.assertEqual(scores[3].rank, 3)
        self.assertEqual(reciprocal_rank_fusion({}, {}, rrf_k=60), {})

    def test_retrieval_config_compatibility_and_cross_field_validation(self):
        legacy = KnowledgeBase.objects.create(name="stage09-legacy", owner=self.user)
        response = self.client.get(f"/api/knowledge-bases/{legacy.id}/retrieval-config/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["fusion_method"], "WEIGHTED")
        self.assertFalse(response.data["data"]["rerank_enabled"])
        url = f"/api/knowledge-bases/{legacy.id}/retrieval-config/"
        self.assertEqual(
            self.client.patch(url, {"rerank_enabled": True}, format="json").status_code,
            400,
        )
        valid = self.client.patch(
            url,
            {
                "retrieval_mode": "HYBRID",
                "fusion_method": "RRF",
                "retrieval_top_k": 5,
                "vector_candidate_k": 20,
                "keyword_candidate_k": 20,
                "rerank_enabled": True,
                "rerank_candidate_k": 10,
            },
            format="json",
        )
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertEqual(
            self.client.patch(url, {"rerank_candidate_k": 4}, format="json").status_code,
            400,
        )
        self.assertEqual(
            self.client.patch(
                url,
                {
                    "vector_candidate_k": 5,
                    "keyword_candidate_k": 5,
                    "rerank_candidate_k": 11,
                },
                format="json",
            ).status_code,
            400,
        )

    def test_independent_top_n_rrf_and_rerank_order(self):
        first = self.add_paragraph(1, "apple celery worker", [1.0, 0.0])
        second = self.add_paragraph(2, "apple redis broker", [0.8, 0.2])
        third = self.add_paragraph(3, "database transaction", [0.0, 1.0])
        self.knowledge.rerank_enabled = True
        self.knowledge.rerank_candidate_k = 2
        self.knowledge.save(update_fields=["rerank_enabled", "rerank_candidate_k"])

        received = []
        def fake_reranker(query, candidates):
            received.extend(item.paragraph_id for item in candidates)
            return RerankOutcome(
                applied=True,
                ordered_ids=[second.id, first.id],
                scores={second.id: 0.9, first.id: 0.5},
            )

        result = retrieve_candidates(
            self.knowledge,
            "apple",
            embedding_function=lambda texts, knowledge: [[1.0, 0.0]],
            reranker_function=fake_reranker,
        )
        self.assertEqual(len(received), 2)
        self.assertEqual(result.ranked_candidate_ids(3)[:2], [second.id, first.id])
        self.assertTrue(result.rerank_applied)
        self.assertEqual(result.candidates[0].rerank_rank, 1)
        self.assertIn(third.id, {item.paragraph_id for item in result.candidates})

    def test_reranker_failure_falls_back_without_modifying_paragraphs(self):
        first = self.add_paragraph(1, "celery task", [1.0, 0.0])
        self.add_paragraph(2, "redis queue", [0.8, 0.2])
        self.knowledge.rerank_enabled = True
        self.knowledge.save(update_fields=["rerank_enabled"])
        before = list(Paragraph.objects.values_list("id", "content", "embedding"))

        def failed_reranker(query, candidates):
            return RerankOutcome(
                applied=False,
                ordered_ids=[item.paragraph_id for item in candidates],
                scores={},
                fallback_code="TIMEOUT",
                fallback_reason="重排超时，已使用RRF结果",
            )

        result = retrieve_candidates(
            self.knowledge,
            "celery",
            embedding_function=lambda texts, knowledge: [[1.0, 0.0]],
            reranker_function=failed_reranker,
        )
        self.assertFalse(result.rerank_applied)
        self.assertEqual(result.rerank_fallback_code, "TIMEOUT")
        self.assertEqual(result.ranked_candidate_ids(1), [first.id])
        self.assertEqual(before, list(Paragraph.objects.values_list("id", "content", "embedding")))

    @override_settings(RERANKER_MODEL_PATH="stage09-mock-model", RERANKER_TIMEOUT_SECONDS=0.001)
    def test_reranker_rejects_invalid_outputs_and_bounds_timeout_work(self):
        from .services import reranker as reranker_service

        candidates = [
            SimpleNamespace(paragraph_id=1, content="first"),
            SimpleNamespace(paragraph_id=2, content="second"),
        ]
        with patch.object(reranker_service, "_predict", return_value=[0.9]):
            mismatch = reranker_service.rerank_candidates("query", candidates)
        self.assertEqual(mismatch.fallback_code, "INVALID_OUTPUT")
        self.assertEqual(mismatch.ordered_ids, [1, 2])

        with patch.object(reranker_service, "_predict", return_value=[float("nan"), 0.2]):
            invalid_number = reranker_service.rerank_candidates("query", candidates)
        self.assertEqual(invalid_number.fallback_code, "INVALID_OUTPUT")

        def slow_prediction(query, items):
            time.sleep(0.05)
            return [0.2, 0.1]

        with patch.object(reranker_service, "_predict", side_effect=slow_prediction):
            timeout = reranker_service.rerank_candidates("query", candidates)
            busy = reranker_service.rerank_candidates("query", candidates)
        self.assertEqual(timeout.fallback_code, "TIMEOUT")
        self.assertEqual(busy.fallback_code, "BUSY")
        time.sleep(0.06)

    def test_capabilities_compare_owner_and_no_persistence(self):
        self.add_paragraph(1, "celery redis queue", [1.0, 0.0])
        capabilities = self.client.get("/api/retrieval/capabilities/")
        self.assertEqual(capabilities.status_code, 200)
        self.assertFalse(capabilities.data["data"]["reranker_configured"])
        self.assertNotIn("model_path", str(capabilities.data))
        before = {
            "mode": self.knowledge.retrieval_mode,
            "fusion": self.knowledge.fusion_method,
            "conversations": Conversation.objects.count(),
            "messages": Message.objects.count(),
        }
        url = f"/api/knowledge-bases/{self.knowledge.id}/retrieval/compare/"
        with patch("api.services.retrieval.embed_texts", return_value=[[1.0, 0.0]]) as embed:
            response = self.client.post(
                url,
                {
                    "query": "celery",
                    "candidate_limit": 20,
                    "experimental": {
                        "retrieval_mode": "HYBRID",
                        "fusion_method": "RRF",
                        "rerank_enabled": False,
                    },
                },
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(embed.call_count, 1)
        self.knowledge.refresh_from_db()
        self.assertEqual(before["mode"], self.knowledge.retrieval_mode)
        self.assertEqual(before["fusion"], self.knowledge.fusion_method)
        self.assertEqual(before["conversations"], Conversation.objects.count())
        self.assertEqual(before["messages"], Message.objects.count())
        self.assertNotIn("embedding", str(response.data))
        other_client = APIClient()
        other_client.force_authenticate(self.other_user)
        self.assertEqual(other_client.post(url, {"query": "x", "experimental": {}}, format="json").status_code, 404)

    def test_evaluation_metrics_and_dataset_integrity(self):
        paragraph = self.add_paragraph(1, "评测目标内容", [1.0, 0.0])
        metrics = evaluate_ranking([999, paragraph.id], {paragraph.id})
        self.assertEqual(metrics["hit_at_1"], 0)
        self.assertEqual(metrics["hit_at_3"], 1)
        self.assertEqual(metrics["recall_at_5"], 1)
        self.assertEqual(metrics["mrr_at_10"], 0.5)
        self.assertGreater(metrics["ndcg_at_10"], 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.jsonl"
            record = {
                "id": "stage09-case",
                "question": "目标是什么？",
                "relevant_targets": [{
                    "document_name": self.document.name,
                    "position": paragraph.position,
                    "content_sha256": content_sha256(paragraph.content),
                }],
                "synthetic": True,
            }
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            cases = load_evaluation_cases(path, self.knowledge)
            self.assertEqual(cases[0].relevant_paragraph_ids, frozenset({paragraph.id}))
            record["relevant_targets"][0]["content_sha256"] = "0" * 64
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(EvaluationDataError):
                load_evaluation_cases(path, self.knowledge)
