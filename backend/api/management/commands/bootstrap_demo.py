from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "创建本地演示用户"

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(username="demo")
        user.set_password("demo123456")
        user.is_staff = True
        user.is_superuser = True
        user.save()
        action = "创建" if created else "更新"
        self.stdout.write(self.style.SUCCESS(f"已{action}演示用户：demo / demo123456"))
