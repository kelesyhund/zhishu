import math
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import (
    Document,
    EmbeddingSpace,
    KnowledgeBase,
    Paragraph,
    ParagraphEmbedding,
    VectorMigrationRun,
    WorkspaceMembership,
)
from .services.pgvector_retrieval import PgvectorCandidates, query_pgvector_candidates
from .services.retrieval import prepare_retrieval
from .services.vector_storage import (
    bulk_write_paragraph_embeddings,
    cancel_migration_run,
    create_migration_run,
    embedding_space_signature,
    eligible_legacy_paragraphs,
    execute_backfill_batch,
    resolve_embedding_space,
    retry_migration_run,
    validate_vectors,
    vector_modes,
    workspace_vector_status,
)
from .services.workspaces import ensure_personal_workspace


class VectorStorageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("stage16-owner", password="pass12345")
        self.workspace = ensure_personal_workspace(self.user)
        self.knowledge_base = KnowledgeBase.objects.create(
            name="stage16-kb", owner=self.user, workspace=self.workspace
        )
        self.document = Document.objects.create(
            knowledge_base=self.knowledge_base,
            name="stage16.txt",
            file="documents/stage16.txt",
            status=Document.Status.SUCCESS,
            paragraph_count=2,
        )
        self.paragraphs = [
            Paragraph.objects.create(
                document=self.document, position=index, content=f"stage16-{index}",
                content_sha256=str(index) * 64, embedding=[1.0, 0.0, float(index - 1)],
            )
            for index in (1, 2)
        ]

    def test_space_isolated_by_workspace_signature_and_dimension(self):
        first = resolve_embedding_space(self.knowledge_base, 3, create=True)
        self.assertEqual(first.signature, "local-hash-256")
        self.assertEqual(resolve_embedding_space(self.knowledge_base, 3, create=True).id, first.id)
        other = resolve_embedding_space(self.knowledge_base, 4, create=True)
        self.assertNotEqual(first.id, other.id)

    def test_vector_validation_rejects_dimensions_and_non_finite_values(self):
        self.assertEqual(validate_vectors([[1.0, 0.0], [0.0, 1.0]]), 2)
        with self.assertRaises(Exception):
            validate_vectors([[1.0], [1.0, 2.0]])
        with self.assertRaises(Exception):
            validate_vectors([[math.nan]])

    @override_settings(VECTOR_WRITE_MODE="LEGACY")
    def test_legacy_write_does_not_create_pgvector_rows(self):
        self.assertIsNone(bulk_write_paragraph_embeddings(self.knowledge_base, self.paragraphs, [[1, 0, 0], [0, 1, 0]]))
        self.assertEqual(ParagraphEmbedding.objects.count(), 0)

    @override_settings(VECTOR_WRITE_MODE="DUAL")
    def test_dual_write_uses_one_space_and_vector_rows(self):
        space = bulk_write_paragraph_embeddings(
            self.knowledge_base, self.paragraphs, [[1, 0, 0], [0, 1, 0]]
        )
        self.assertEqual(space.status, EmbeddingSpace.Status.READY)
        self.assertEqual(ParagraphEmbedding.objects.filter(space=space).count(), 2)
        self.assertEqual(ParagraphEmbedding.objects.first().knowledge_base_id, self.knowledge_base.id)
        self.assertEqual(list(ParagraphEmbedding.objects.first().embedding), [1.0, 0.0, 0.0])

    @override_settings(VECTOR_WRITE_MODE="DUAL")
    def test_dual_write_rejects_paragraph_from_another_knowledge_base(self):
        other = KnowledgeBase.objects.create(
            name="stage16-other-local", owner=self.user, workspace=self.workspace
        )
        other_document = Document.objects.create(
            knowledge_base=other,
            name="other.txt",
            file="documents/other.txt",
            status=Document.Status.SUCCESS,
        )
        foreign_paragraph = Paragraph.objects.create(
            document=other_document, position=1, content="foreign", embedding=[1, 0, 0]
        )
        with self.assertRaises(Exception):
            bulk_write_paragraph_embeddings(
                self.knowledge_base, [foreign_paragraph], [[1, 0, 0]]
            )
        self.assertEqual(ParagraphEmbedding.objects.count(), 0)

    def test_backfill_is_idempotent_and_resumes_from_cursor(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        run = create_migration_run(self.workspace, space, self.user, 10)
        first = execute_backfill_batch(run.id)
        self.assertEqual(first.processed, 2)
        run.refresh_from_db()
        self.assertEqual(run.cursor_id, self.paragraphs[-1].id)
        execute_backfill_batch(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, VectorMigrationRun.Status.SUCCESS)
        self.assertEqual(ParagraphEmbedding.objects.count(), 2)
        self.assertEqual(run.succeeded_count, 2)

    def test_cancelled_backfill_keeps_existing_rows(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        ParagraphEmbedding.objects.create(
            paragraph=self.paragraphs[0], knowledge_base=self.knowledge_base,
            space=space, embedding=[1, 0, 0], dimension=3,
            content_hash=self.paragraphs[0].content_sha256,
        )
        run = create_migration_run(self.workspace, space, self.user, 10)
        run.status = VectorMigrationRun.Status.CANCEL_REQUESTED
        run.save(update_fields=["status"])
        execute_backfill_batch(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, VectorMigrationRun.Status.CANCELLED)
        self.assertEqual(ParagraphEmbedding.objects.count(), 1)

    def test_invalid_legacy_vector_marks_run_and_space_degraded(self):
        space = resolve_embedding_space(self.knowledge_base, 2, create=True)
        run = create_migration_run(self.workspace, space, self.user, 10)
        execute_backfill_batch(run.id)
        execute_backfill_batch(run.id)
        run.refresh_from_db(); space.refresh_from_db()
        self.assertEqual(run.status, VectorMigrationRun.Status.FAILURE)
        self.assertEqual(run.failed_count, 2)
        self.assertEqual(space.status, EmbeddingSpace.Status.DEGRADED)

    @override_settings(VECTOR_WRITE_MODE="DUAL")
    def test_paragraph_delete_cascades_pgvector_row(self):
        bulk_write_paragraph_embeddings(self.knowledge_base, self.paragraphs, [[1, 0, 0], [0, 1, 0]])
        self.paragraphs[0].delete()
        self.assertEqual(ParagraphEmbedding.objects.count(), 1)

    def test_only_one_active_migration_per_space(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        first = create_migration_run(self.workspace, space, self.user, 10)
        second = create_migration_run(self.workspace, space, self.user, 20)
        self.assertEqual(first.id, second.id)

    def test_status_contains_counts_but_not_vector_values(self):
        payload = workspace_vector_status(self.workspace)
        self.assertEqual(payload["legacy_vector_count"], 2)
        self.assertNotIn("embedding", str(payload))

    def test_local_space_signature_contains_no_secret(self):
        signature, model_name, revision, config = embedding_space_signature(self.knowledge_base)
        self.assertEqual((signature, model_name, revision, config), ("local-hash-256", "local-hash-256", 1, None))

    def test_default_modes_are_legacy_and_report_database_vendor(self):
        modes = vector_modes()
        self.assertEqual(modes["write_mode"], "LEGACY")
        self.assertEqual(modes["read_mode"], "LEGACY")
        self.assertEqual(modes["database_vendor"], "sqlite")

    def test_space_unique_constraint_prevents_duplicate(self):
        resolve_embedding_space(self.knowledge_base, 3, create=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmbeddingSpace.objects.create(
                workspace=self.workspace, signature="local-hash-256", model_name="duplicate", dimension=3
            )

    def test_space_dimension_constraint_rejects_zero(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmbeddingSpace.objects.create(
                workspace=self.workspace, signature="bad", model_name="bad", dimension=0
            )

    def test_migration_batch_size_is_clamped_to_safe_minimum(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        self.assertEqual(create_migration_run(self.workspace, space, self.user, 1).batch_size, 10)

    def test_migration_batch_size_is_clamped_to_safe_maximum(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        self.assertEqual(create_migration_run(self.workspace, space, self.user, 99999).batch_size, 2000)

    def test_only_success_documents_are_backfill_candidates(self):
        failed = Document.objects.create(
            knowledge_base=self.knowledge_base, name="stage16-failed.txt", file="documents/failed.txt",
            status=Document.Status.FAILURE,
        )
        Paragraph.objects.create(document=failed, position=1, content="ignored", embedding=[1, 0, 0])
        self.assertEqual(eligible_legacy_paragraphs(self.workspace, "", 3).count(), 2)

    def test_second_completed_backfill_counts_existing_rows_as_skipped(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        first = create_migration_run(self.workspace, space, self.user, 10)
        execute_backfill_batch(first.id); execute_backfill_batch(first.id)
        second = create_migration_run(self.workspace, space, self.user, 10)
        execute_backfill_batch(second.id); execute_backfill_batch(second.id)
        second.refresh_from_db()
        self.assertEqual((second.succeeded_count, second.skipped_count), (0, 2))

    def test_retry_failure_resets_cursor_and_counts(self):
        space = resolve_embedding_space(self.knowledge_base, 2, create=True)
        run = create_migration_run(self.workspace, space, self.user, 10)
        execute_backfill_batch(run.id); execute_backfill_batch(run.id); run.refresh_from_db()
        with patch("api.services.vector_storage.dispatch_migration_run", side_effect=lambda item: item):
            retried = retry_migration_run(run)
        self.assertEqual((retried.status, retried.cursor_id, retried.failed_count), ("PENDING", 0, 0))

    def test_cancel_pending_migration_requests_cooperative_stop(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        run = create_migration_run(self.workspace, space, self.user, 10)
        self.assertEqual(cancel_migration_run(run).status, VectorMigrationRun.Status.CANCEL_REQUESTED)

    def test_cancel_successful_migration_is_noop(self):
        space = resolve_embedding_space(self.knowledge_base, 3, create=True)
        run = create_migration_run(self.workspace, space, self.user, 10)
        run.status = VectorMigrationRun.Status.SUCCESS; run.save(update_fields=["status"])
        self.assertEqual(cancel_migration_run(run).status, VectorMigrationRun.Status.SUCCESS)

    def test_pgvector_query_explicitly_falls_back_on_sqlite(self):
        result = query_pgvector_candidates(self.knowledge_base, [1, 0, 0], 5)
        self.assertEqual(result.fallback_code, "DATABASE_NOT_POSTGRESQL")

    @override_settings(VECTOR_READ_MODE="PGVECTOR")
    def test_pgvector_read_mode_falls_back_to_legacy_candidates_when_unavailable(self):
        prepared = prepare_retrieval(
            self.knowledge_base, "stage16", embedding_function=lambda texts, kb: [[1, 0, 0]]
        )
        self.assertEqual(len(prepared.candidates), 2)

    @override_settings(VECTOR_READ_MODE="SHADOW", VECTOR_SHADOW_SAMPLE_RATE=0)
    @patch("api.services.retrieval.query_pgvector_candidates")
    def test_zero_shadow_sample_does_not_query_pgvector(self, query_mock):
        prepare_retrieval(self.knowledge_base, "stage16", embedding_function=lambda texts, kb: [[1, 0, 0]])
        query_mock.assert_not_called()

    @override_settings(VECTOR_READ_MODE="SHADOW", VECTOR_SHADOW_SAMPLE_RATE=1)
    @patch("api.services.retrieval.query_pgvector_candidates", return_value=PgvectorCandidates({}, (), 0, "NO_SPACE"))
    def test_full_shadow_sample_executes_safe_side_query(self, query_mock):
        prepare_retrieval(self.knowledge_base, "stage16", embedding_function=lambda texts, kb: [[1, 0, 0]])
        query_mock.assert_called_once()

    @override_settings(VECTOR_WRITE_MODE="DUAL")
    def test_workspace_coverage_reaches_one_after_dual_write(self):
        bulk_write_paragraph_embeddings(self.knowledge_base, self.paragraphs, [[1, 0, 0], [0, 1, 0]])
        self.assertEqual(workspace_vector_status(self.workspace)["coverage_ratio"], 1.0)

    @override_settings(VECTOR_WRITE_MODE="DUAL")
    def test_document_delete_cascades_all_pgvector_rows(self):
        bulk_write_paragraph_embeddings(self.knowledge_base, self.paragraphs, [[1, 0, 0], [0, 1, 0]])
        self.document.delete()
        self.assertEqual(ParagraphEmbedding.objects.count(), 0)

    def test_hnsw_command_refuses_sqlite(self):
        with self.assertRaises(CommandError):
            call_command("manage_vector_index", 1, stdout=StringIO(), stderr=StringIO())

    def test_benchmark_command_refuses_sqlite(self):
        with self.assertRaises(CommandError):
            call_command("benchmark_pgvector", self.knowledge_base.id, stdout=StringIO(), stderr=StringIO())

    def test_benchmark_seed_command_refuses_sqlite(self):
        with self.assertRaises(CommandError):
            call_command("seed_pgvector_benchmark", 10000, stdout=StringIO(), stderr=StringIO())


class VectorApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("stage16-api-admin", password="pass12345")
        self.workspace = ensure_personal_workspace(self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_WORKSPACE_ID": str(self.workspace.id)}

    def test_admin_can_read_vector_status(self):
        response = self.client.get("/api/vector-index/status/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("coverage_ratio", response.data["data"])
        self.assertNotIn("connection", str(response.data).lower())

    def test_other_workspace_migration_returns_404(self):
        other = User.objects.create_user("stage16-other", password="pass12345")
        other_workspace = ensure_personal_workspace(other)
        other_kb = KnowledgeBase.objects.create(name="stage16-other-kb", owner=other, workspace=other_workspace)
        space = resolve_embedding_space(other_kb, 3, create=True)
        run = VectorMigrationRun.objects.create(workspace=other_workspace, space=space, created_by=other)
        response = self.client.get(f"/api/vector-index/migrations/{run.id}/", **self.headers)
        self.assertEqual(response.status_code, 404)

    @patch("api.vector_views.dispatch_migration_run", side_effect=lambda run: run)
    def test_sqlite_rejects_real_migration_without_leaking_data(self, _dispatch):
        kb = KnowledgeBase.objects.create(name="stage16-api-kb", owner=self.user, workspace=self.workspace)
        doc = Document.objects.create(
            knowledge_base=kb, name="stage16-api.txt", file="documents/stage16-api.txt",
            status=Document.Status.SUCCESS,
        )
        Paragraph.objects.create(document=doc, position=1, content="safe", embedding=[1, 0, 0])
        response = self.client.post(
            "/api/vector-index/migrations/", {"knowledge_base_id": kb.id, "batch_size": 20},
            format="json", **self.headers,
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("embedding", str(response.data).lower())
        self.assertNotIn("celery_task_id", str(response.data))

    def test_migration_list_has_bounded_pagination_contract(self):
        response = self.client.get("/api/vector-index/migrations/?page=1&page_size=999", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["page_size"], 100)
        self.assertIn("total_pages", response.data["data"])

    def test_unknown_migration_action_returns_404(self):
        kb = KnowledgeBase.objects.create(name="stage16-action", owner=self.user, workspace=self.workspace)
        space = resolve_embedding_space(kb, 3, create=True)
        run = VectorMigrationRun.objects.create(workspace=self.workspace, space=space, created_by=self.user)
        response = self.client.post(f"/api/vector-index/migrations/{run.id}/unknown/", **self.headers)
        self.assertEqual(response.status_code, 404)

    def test_knowledge_base_vector_status_is_workspace_scoped(self):
        other = User.objects.create_user("stage16-status-other", password="pass12345")
        other_workspace = ensure_personal_workspace(other)
        kb = KnowledgeBase.objects.create(name="stage16-hidden", owner=other, workspace=other_workspace)
        response = self.client.get(f"/api/knowledge-bases/{kb.id}/vector-status/", **self.headers)
        self.assertEqual(response.status_code, 404)

    def test_viewer_cannot_open_vector_operations(self):
        viewer = User.objects.create_user("stage16-viewer", password="pass12345")
        WorkspaceMembership.objects.create(
            workspace=self.workspace, user=viewer, role=WorkspaceMembership.Role.VIEWER, created_by=self.user
        )
        self.client.force_authenticate(viewer)
        response = self.client.get("/api/vector-index/status/", **self.headers)
        self.assertEqual(response.status_code, 403)

    def test_vector_verify_response_contains_only_aggregate_differences(self):
        kb = KnowledgeBase.objects.create(name="stage16-verify", owner=self.user, workspace=self.workspace)
        doc = Document.objects.create(
            knowledge_base=kb, name="verify.txt", file="documents/verify.txt", status=Document.Status.SUCCESS
        )
        paragraph = Paragraph.objects.create(document=doc, position=1, content="verify", embedding=[1, 0, 0])
        space = resolve_embedding_space(kb, 3, create=True)
        ParagraphEmbedding.objects.create(
            paragraph=paragraph, knowledge_base=kb, space=space,
            embedding=[1, 0, 0], dimension=3, content_hash="a" * 64
        )
        response = self.client.post(f"/api/knowledge-bases/{kb.id}/vector-verify/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["mismatched_count"], 0)
        self.assertNotIn("embedding", str(response.data).lower())
