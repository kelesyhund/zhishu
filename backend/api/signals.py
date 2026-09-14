from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

from .models import AccountProfile, Document


@receiver(post_delete, sender=Document)
def delete_document_file(sender, instance, **kwargs):
    """数据库删除提交后，清理 FileField 对应的实际文件。"""

    if not instance.file or not instance.file.name:
        return

    storage = instance.file.storage
    file_name = instance.file.name
    transaction.on_commit(lambda: storage.delete(file_name))


@receiver(post_save, sender=User)
def create_personal_workspace(sender, instance, created, **kwargs):
    if not created:
        return
    AccountProfile.objects.get_or_create(user=instance)
    from .services.workspaces import ensure_personal_workspace

    ensure_personal_workspace(instance)
