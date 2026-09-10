import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.authtoken.models import Token

from api.models import KnowledgeBase


class Command(BaseCommand):
    help = "在独立Stage 8数据库中准备或清理可识别的Locust临时数据"

    def add_arguments(self, parser):
        parser.add_argument("--cleanup", action="store_true")

    def handle(self, *args, **options):
        username = os.getenv("STAGE08_USERNAME", "stage08-load-user")
        password = os.getenv("STAGE08_PASSWORD", "stage08-load-password")
        knowledge_name = os.getenv("STAGE08_KNOWLEDGE_NAME", "stage08-loadtest-kb")
        auth_token = os.getenv(
            "STAGE08_AUTH_TOKEN",
            "stage08-load-token-000000000000000000000",
        )
        if not username.startswith("stage08-") or not knowledge_name.startswith("stage08-"):
            raise CommandError("只允许操作stage08-前缀的临时数据")

        if options["cleanup"]:
            user = User.objects.filter(username=username).first()
            if user:
                KnowledgeBase.objects.filter(owner=user, name__startswith="stage08-").delete()
                user.delete()
            self.stdout.write(self.style.SUCCESS("stage08临时压测数据已清理"))
            return

        with transaction.atomic():
            user, _ = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.save(update_fields=["password"])
            knowledge, _ = KnowledgeBase.objects.get_or_create(
                owner=user,
                name=knowledge_name,
                defaults={"description": "Stage 8异步上传压测专用临时知识库"},
            )
            Token.objects.filter(user=user).delete()
            Token.objects.create(user=user, key=auth_token)
        self.stdout.write(self.style.SUCCESS(f"stage08压测数据已准备，knowledge_id={knowledge.id}"))
