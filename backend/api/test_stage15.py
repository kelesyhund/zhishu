import json
import logging
import re
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .logging_filters import ApplicationSecretFilter, JsonLogFormatter
from .models import Document, DocumentProcessingTask, KnowledgeBase, Paragraph
from .observability import HTTP_REQUESTS_TOTAL
from .observability import REQUEST_ID
from .services.document_tasks import create_processing_task, dispatch_processing_task
from .services.task_recovery import STALE_MESSAGE, reconcile_stale_processing_tasks


class HealthAndRequestIdTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_live_returns_up_and_request_id(self):
        response = self.client.get("/health/live/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "UP")
        self.assertRegex(response["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_valid_request_id_is_reused_and_invalid_value_is_replaced(self):
        valid = self.client.get("/health/live/", HTTP_X_REQUEST_ID="stage15-valid_01")
        self.assertEqual(valid["X-Request-ID"], "stage15-valid_01")
        invalid = self.client.get("/health/live/", HTTP_X_REQUEST_ID="x" * 200)
        self.assertNotEqual(invalid["X-Request-ID"], "x" * 200)
        self.assertRegex(invalid["X-Request-ID"], r"^[a-f0-9]{32}$")

    @patch("api.health_views._redis_status", return_value=(True, "UP"))
    def test_ready_is_up_when_required_dependencies_are_available(self, redis_status):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "UP")

    @patch("api.health_views._database_status", return_value=(False, "DOWN"))
    @patch("api.health_views._redis_status", return_value=(True, "UP"))
    def test_ready_returns_503_when_database_is_unavailable(self, redis_status, database_status):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["data"]["checks"]["database"], "DOWN")

    @patch("api.health_views._redis_status", return_value=(False, "DOWN"))
    def test_ready_returns_503_when_redis_is_unavailable(self, redis_status):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["data"]["checks"]["redis"], "DOWN")

    def test_dependency_detail_rejects_anonymous_user(self):
        response = self.client.get("/health/dependencies/")
        self.assertIn(response.status_code, {401, 403})

    def test_metrics_exist_but_nginx_blocks_public_route(self):
        HTTP_REQUESTS_TOTAL.labels("GET", "/stage15-test", "2xx").inc()
        response = self.client.get("/internal/metrics/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("http_requests_total", body)
        nginx = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "nginx.conf"
        self.assertIn("location = /internal/metrics/", nginx.read_text(encoding="utf-8"))
        self.assertRegex(nginx.read_text(encoding="utf-8"), r"location = /internal/metrics/\s*\{[^}]*return 404")


class StructuredLogTests(TestCase):
    def test_json_log_contains_correlation_fields_and_redacts_secrets(self):
        record = logging.LogRecord(
            "stage15", logging.INFO, __file__, 1,
            "model key sk-stage15-temporary-secret cookie=session-value %s",
            ({"api_key": "sk-stage15-dict-secret", "token": "sensitive"},),
            None,
        )
        record.event = "stage15.test"
        record.request_id = "stage15-request"
        record.trace_id = "0" * 32
        ApplicationSecretFilter().filter(record)
        payload = json.loads(JsonLogFormatter().format(record))
        encoded = json.dumps(payload)
        self.assertEqual(payload["request_id"], "stage15-request")
        self.assertEqual(payload["trace_id"], "0" * 32)
        self.assertNotIn("sk-stage15", encoded)
        self.assertNotIn("session-value", encoded)
        self.assertNotIn("sensitive", encoded)


@override_settings(STALE_TASK_THRESHOLD_SECONDS=420)
class StaleTaskRecoveryTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.temp_media = TemporaryDirectory()
        self.override_media = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.override_media.enable()
        self.user = User.objects.create_user("stage15-recovery-user", password="unused-stage15-password")
        self.knowledge = KnowledgeBase.objects.create(name="stage15 recovery kb", owner=self.user)

    def tearDown(self):
        self.override_media.disable()
        self.temp_media.cleanup()

    def document(self, name):
        return Document.objects.create(
            knowledge_base=self.knowledge,
            name=name,
            file=SimpleUploadedFile(name, b"stage15 temporary recovery content"),
            status=Document.Status.PROCESSING,
        )

    def stale_task(self, document, status=DocumentProcessingTask.Status.PROCESSING):
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.REPROCESS,
            status=status,
            current_stage=DocumentProcessingTask.Stage.EMBEDDING,
        )
        stale_at = timezone.now() - timedelta(seconds=600)
        DocumentProcessingTask.objects.filter(pk=task.pk).update(updated_at=stale_at)
        task.refresh_from_db()
        return task

    def test_stale_task_converges_and_preserves_usable_paragraphs(self):
        document = self.document("stage15-stale-preserve.txt")
        Paragraph.objects.create(document=document, position=0, content="old usable chunk", embedding=[1.0])
        document.status = Document.Status.SUCCESS
        document.paragraph_count = 1
        document.save(update_fields=["status", "paragraph_count"])
        task = self.stale_task(document)

        self.assertEqual(reconcile_stale_processing_tasks(), 1)
        task.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(task.status, DocumentProcessingTask.Status.FAILURE)
        self.assertEqual(task.error_message, STALE_MESSAGE)
        self.assertEqual(document.status, Document.Status.SUCCESS)
        self.assertEqual(document.paragraphs.count(), 1)

    def test_stale_upload_without_paragraphs_marks_document_failure(self):
        document = self.document("stage15-stale-empty.txt")
        task = self.stale_task(document, DocumentProcessingTask.Status.PENDING)
        task.task_type = DocumentProcessingTask.TaskType.UPLOAD
        task.save(update_fields=["task_type"])

        self.assertEqual(reconcile_stale_processing_tasks(), 1)
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILURE)
        self.assertEqual(document.paragraph_count, 0)

    def test_recent_task_is_not_misclassified_and_repeated_scan_is_idempotent(self):
        document = self.document("stage15-recent.txt")
        task = DocumentProcessingTask.objects.create(
            document=document,
            task_type=DocumentProcessingTask.TaskType.UPLOAD,
        )
        self.assertEqual(reconcile_stale_processing_tasks(), 0)
        task.refresh_from_db()
        self.assertEqual(task.status, DocumentProcessingTask.Status.PENDING)

        DocumentProcessingTask.objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(seconds=600)
        )
        self.assertEqual(reconcile_stale_processing_tasks(), 1)
        self.assertEqual(reconcile_stale_processing_tasks(), 0)


class DeploymentConfigurationTests(TestCase):
    def test_compose_and_nginx_do_not_use_development_server_or_public_data_ports(self):
        root = Path(__file__).resolve().parents[2]
        compose = (root / "docker-compose.production.yml").read_text(encoding="utf-8")
        nginx = (root / "deploy" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
        self.assertNotIn("runserver", compose)
        self.assertIn("CELERY_TASK_ALWAYS_EAGER: \"false\"", compose)
        postgres_block = compose.split("\n  postgres:", 1)[1].split("\n  redis:", 1)[0]
        redis_block = compose.split("\n  redis:", 1)[1].split("\n  migrate:", 1)[0]
        self.assertNotIn("ports:", postgres_block)
        self.assertNotIn("ports:", redis_block)
        self.assertIn("proxy_buffering off", nginx)
        self.assertIn("try_files $uri $uri/ /index.html", nginx)
        self.assertIn("location /_protected_media/", nginx)
        self.assertIn("internal;", nginx)

    def test_prometheus_labels_do_not_contain_forbidden_high_cardinality_fields(self):
        source = (Path(__file__).resolve().parent / "observability.py").read_text(encoding="utf-8")
        metric_declarations = "\n".join(
            line for line in source.splitlines() if "Counter(" in line or "Gauge(" in line or "Histogram(" in line
        )
        for forbidden in ("request_id", "trace_id", "username", "conversation_id", "document_id"):
            self.assertNotIn(forbidden, metric_declarations)


class ProtectedMediaTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.temp_media = TemporaryDirectory()
        self.override_media = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.override_media.enable()
        self.owner = User.objects.create_user("stage15-media-owner", password="stage15-test-password")
        self.other = User.objects.create_user("stage15-media-other", password="stage15-test-password")
        self.knowledge = KnowledgeBase.objects.create(name="stage15 media kb", owner=self.owner)
        self.document = Document.objects.create(
            knowledge_base=self.knowledge,
            name="stage15-protected.txt",
            file=SimpleUploadedFile("stage15-protected.txt", b"protected stage15 content"),
        )

    def tearDown(self):
        self.override_media.disable()
        self.temp_media.cleanup()

    @override_settings(DEBUG=True)
    def test_owner_can_read_file_but_other_user_gets_404(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        own = client.get(f"/media/{self.document.file.name}")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(b"".join(own.streaming_content), b"protected stage15 content")
        client.force_authenticate(self.other)
        denied = client.get(f"/media/{self.document.file.name}")
        self.assertEqual(denied.status_code, 404)


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class CeleryPropagationTests(TestCase):
    def setUp(self):
        self.temp_media = TemporaryDirectory()
        self.override_media = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.override_media.enable()

    def tearDown(self):
        self.override_media.disable()
        self.temp_media.cleanup()

    def test_dispatch_propagates_request_and_trace_headers_after_commit(self):
        from django.contrib.auth.models import User

        user = User.objects.create_user("stage15-trace-user", password="unused-stage15-password")
        knowledge = KnowledgeBase.objects.create(name="stage15 trace kb", owner=user)
        document = Document.objects.create(
            knowledge_base=knowledge,
            name="stage15-trace.txt",
            file=SimpleUploadedFile("stage15-trace.txt", b"temporary"),
        )
        task, _ = create_processing_task(document, DocumentProcessingTask.TaskType.UPLOAD)
        request_token = REQUEST_ID.set("stage15-request-id")

        def inject_headers(headers):
            headers["traceparent"] = "00-11111111111111111111111111111111-2222222222222222-01"

        try:
            with patch("api.telemetry_compat.inject", side_effect=inject_headers), patch(
                "api.tasks.process_document_task.apply_async",
                return_value=SimpleNamespace(id="stage15-celery-id"),
            ) as apply_async:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_processing_task(task.id)
        finally:
            REQUEST_ID.reset(request_token)
        headers = apply_async.call_args.kwargs["headers"]
        self.assertEqual(headers["x-request-id"], "stage15-request-id")
        self.assertTrue(headers["traceparent"].startswith("00-1111"))
