import re
from time import perf_counter

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from api.models import EmbeddingSpace


ALLOWED_DIMENSIONS = {256, 384, 768, 1024, 1536}


class Command(BaseCommand):
    help = "为单个EmbeddingSpace创建受控HNSW部分索引"

    def add_arguments(self, parser):
        parser.add_argument("space_id", type=int)
        parser.add_argument("--m", type=int, default=16)
        parser.add_argument("--ef-construction", type=int, default=64)
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="以CONCURRENTLY方式重建该受控索引；不会删除向量数据",
        )
        parser.add_argument(
            "--drop-only",
            action="store_true",
            help="仅删除该受控索引并将空间标记为未索引；不会删除向量数据",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("HNSW索引只能在PostgreSQL + pgvector环境创建")
        space = EmbeddingSpace.objects.filter(pk=options["space_id"]).first()
        if not space:
            raise CommandError("向量空间不存在")
        dimension = int(space.dimension)
        m = int(options["m"])
        ef = int(options["ef_construction"])
        if dimension not in ALLOWED_DIMENSIONS:
            raise CommandError(f"维度{dimension}不在HNSW白名单；可继续使用Exact检索")
        if not 4 <= m <= 64 or not 16 <= ef <= 512:
            raise CommandError("HNSW参数超出安全范围")
        index_name = f"pe_hnsw_s{space.id}_d{dimension}"
        if not re.fullmatch(r"[a-z0-9_]+", index_name):
            raise CommandError("索引名不安全")
        quoted = connection.ops.quote_name(index_name)
        if options["drop_only"]:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {quoted}")
            space.indexed = False
            space.save(update_fields=["indexed", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"已删除索引 {index_name}，向量数据保留"))
            return
        sql = (
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {quoted} ON api_paragraphembedding "
            f"USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) "
            f"WITH (m = {m}, ef_construction = {ef}) "
            f"WHERE space_id = {space.id} AND dimension = {dimension}"
        )
        started = perf_counter()
        with connection.cursor() as cursor:
            if options["rebuild"]:
                cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {quoted}")
                space.indexed = False
                space.save(update_fields=["indexed", "updated_at"])
            cursor.execute(sql)
        space.indexed = True
        space.save(update_fields=["indexed", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(
                f"已创建索引 {index_name}，耗时 {perf_counter() - started:.3f} 秒"
            )
        )
