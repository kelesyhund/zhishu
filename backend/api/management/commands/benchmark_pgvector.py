import csv
import json
import os
import platform
import statistics
from pathlib import Path
from time import perf_counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test import override_settings

from api.models import KnowledgeBase, Paragraph
from api.services.pgvector_retrieval import query_pgvector_candidates
from api.services.vector_retrieval import calculate_vector_scores
from api.services.vector_storage import resolve_embedding_space


def percentile(values, quantile):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))))]


class Command(BaseCommand):
    help = "对受保护的临时知识库比较 Legacy、pgvector Exact 和 HNSW"

    def add_arguments(self, parser):
        parser.add_argument("knowledge_base_id", type=int)
        parser.add_argument("--queries", type=int, default=20)
        parser.add_argument("--output", default="../evals/results/pgvector-benchmark.json")
        parser.add_argument(
            "--skip-legacy",
            action="store_true",
            help="调优HNSW时跳过昂贵的Python Legacy基线，但仍以Exact作为召回真值",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("基准必须在真实PostgreSQL + pgvector环境运行")
        kb = KnowledgeBase.objects.filter(pk=options["knowledge_base_id"]).first()
        if not kb or not kb.name.startswith("pgbench-"):
            raise CommandError("只允许对 pgbench-* 临时知识库执行基准")
        paragraph_queryset = (
            Paragraph.objects.filter(document__knowledge_base=kb)
            .exclude(embedding=[])
            .only("id", "embedding")
            .order_by("id")
        )
        paragraph_count = paragraph_queryset.count()
        if not paragraph_count:
            raise CommandError("临时知识库没有Legacy向量")
        query_count = max(1, min(int(options["queries"]), 200))
        query_paragraphs = list(paragraph_queryset[:query_count])
        query_vectors = [paragraph.embedding for paragraph in query_paragraphs]
        paragraphs = None if options["skip_legacy"] else list(paragraph_queryset)
        rows = []
        for index, vector in enumerate(query_vectors, start=1):
            legacy_ids = []
            if paragraphs is not None:
                started = perf_counter()
                legacy = calculate_vector_scores(vector, paragraphs)
                legacy_ids = sorted(legacy, key=lambda pk: (-legacy[pk].normalized, pk))[:10]
                legacy_ms = (perf_counter() - started) * 1000
            with override_settings(VECTOR_SEARCH_MODE="EXACT"):
                exact = query_pgvector_candidates(kb, vector, 10)
            with override_settings(VECTOR_SEARCH_MODE="HNSW"):
                hnsw = query_pgvector_candidates(kb, vector, 10)
            if exact.fallback_code or hnsw.fallback_code:
                raise CommandError(f"空间尚未READY：{exact.fallback_code or hnsw.fallback_code}")
            if paragraphs is not None:
                rows.append({"query": index, "backend": "legacy", "latency_ms": legacy_ms, "recall_at_10": 1.0})
            rows.extend([
                {"query": index, "backend": "pgvector_exact", "latency_ms": exact.latency_ms,
                 "recall_at_10": (
                     len(set(legacy_ids) & set(exact.paragraph_ids)) / max(1, len(set(legacy_ids)))
                     if legacy_ids else 1.0
                 )},
                {"query": index, "backend": "pgvector_hnsw", "latency_ms": hnsw.latency_ms,
                 "recall_at_10": len(set(exact.paragraph_ids) & set(hnsw.paragraph_ids)) / max(1, len(set(exact.paragraph_ids)))},
            ])
        summary = {}
        for backend in {row["backend"] for row in rows}:
            selected = [row for row in rows if row["backend"] == backend]
            latencies = [row["latency_ms"] for row in selected]
            summary[backend] = {
                "count": len(selected), "p50_ms": round(statistics.median(latencies), 3),
                "p95_ms": round(percentile(latencies, .95), 3), "p99_ms": round(percentile(latencies, .99), 3),
                "mean_ms": round(statistics.mean(latencies), 3),
                "sequential_qps": round(1000 / max(statistics.mean(latencies), 0.001), 3),
                "mean_recall_at_10": round(statistics.mean(row["recall_at_10"] for row in selected), 6),
            }
        dimension = len(query_vectors[0])
        space = resolve_embedding_space(kb, dimension, ready=True)
        metadata, plans = self._database_evidence(kb, space, query_vectors[0], dimension)
        output = Path(options["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "knowledge_base_id": kb.id, "knowledge_base_name": kb.name,
            "paragraph_count": paragraph_count, "dimension": dimension,
            "queries": query_count, "summary": summary, "rows": rows,
            "environment": metadata, "query_plans": plans,
            "legacy_skipped": bool(options["skip_legacy"]),
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        csv_path = output.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["query", "backend", "latency_ms", "recall_at_10"])
            writer.writeheader(); writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f"已保存 {output} 和 {csv_path}"))

    def _database_evidence(self, kb, space, query_vector, dimension):
        vector_literal = "[" + ",".join(str(float(value)) for value in query_vector) + "]"
        index_name = f"pe_hnsw_s{space.id}_d{dimension}"
        sql = f"""
            SELECT pe.paragraph_id
            FROM api_paragraphembedding pe
            WHERE pe.space_id = %s
              AND pe.dimension = %s
              AND pe.knowledge_base_id = %s
            ORDER BY (pe.embedding::vector({dimension})) <=> %s::vector
            LIMIT 100
        """
        plans = {}
        for mode in ("EXACT", "HNSW"):
            with transaction.atomic(), connection.cursor() as cursor:
                if mode == "EXACT":
                    cursor.execute("SET LOCAL enable_indexscan = off")
                else:
                    cursor.execute("SET LOCAL hnsw.ef_search = %s", [settings.VECTOR_HNSW_EF_SEARCH])
                    cursor.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                cursor.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql,
                    [space.id, dimension, kb.id, vector_literal],
                )
                plans[mode.lower()] = "\n".join(row[0] for row in cursor.fetchall())
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            postgresql_version = cursor.fetchone()[0]
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            pgvector_version = cursor.fetchone()[0]
            cursor.execute(
                "SELECT pg_relation_size(%s::regclass), pg_size_pretty(pg_relation_size(%s::regclass)), reloptions "
                "FROM pg_class WHERE oid = %s::regclass",
                [index_name, index_name, index_name],
            )
            index_size_bytes, index_size_pretty, index_options = cursor.fetchone()
            cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            database_size_pretty = cursor.fetchone()[0]
        return {
            "postgresql_version": postgresql_version,
            "pgvector_version": pgvector_version,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "hnsw_ef_search": settings.VECTOR_HNSW_EF_SEARCH,
            "hnsw_index": index_name,
            "hnsw_index_size_bytes": index_size_bytes,
            "hnsw_index_size_pretty": index_size_pretty,
            "hnsw_index_options": index_options or [],
            "database_size_pretty": database_size_pretty,
            "cache_state": "warm-after-seed-and-index",
            "concurrency": 1,
        }, plans
