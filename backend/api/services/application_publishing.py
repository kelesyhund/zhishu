from django.db import transaction
from django.db.models import Max

from api.models import Application, ApplicationVersion, Document

from .agent_tools.registry import TOOL_REGISTRY
from .model_resolution import document_needs_reprocess


class ApplicationPublishingError(Exception):
    def __init__(self, message, error_code="PUBLISH_VALIDATION_FAILED", status_code=400):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


def _validate(application):
    links = list(
        application.knowledge_links.filter(enabled=True)
        .select_related("knowledge_base__embedding_model_config")
        .order_by("position", "id")
    )
    if not links:
        raise ApplicationPublishingError("发布前至少绑定一个启用的知识库")
    if len(links) > 5:
        raise ApplicationPublishingError("一个应用最多绑定五个知识库")
    if any(link.knowledge_base.owner_id != application.owner_id for link in links):
        raise ApplicationPublishingError("应用包含无权访问的知识库")
    usable = 0
    for link in links:
        documents = Document.objects.filter(
            knowledge_base=link.knowledge_base,
            status=Document.Status.SUCCESS,
        ).select_related("knowledge_base__embedding_model_config")
        usable += sum(1 for document in documents if not document_needs_reprocess(document))
    if usable == 0:
        raise ApplicationPublishingError("关联知识库中没有可参与检索的有效文档")
    if application.chat_model_config_id:
        config = application.chat_model_config
        if config.owner_id != application.owner_id or config.model_type != config.ModelType.CHAT:
            raise ApplicationPublishingError("Chat模型配置无效")
        if config.last_test_status != config.TestStatus.SUCCESS:
            raise ApplicationPublishingError("发布前请先完成Chat模型连通性测试")
    for name in application.enabled_tools:
        tool = TOOL_REGISTRY.get(name)
        if not tool:
            raise ApplicationPublishingError(f"工具 {name} 不存在")
        if not tool.allow_public or tool.side_effect_level != "NONE" or tool.requires_owner:
            raise ApplicationPublishingError(f"工具 {name} 不允许公开应用调用")
    if application.agent_enabled and len(links) != 1:
        raise ApplicationPublishingError("当前版本的Agent应用只能绑定一个知识库")
    return links


def build_snapshot(application, links):
    config = application.chat_model_config
    return {
        "schema_version": 1,
        "name": application.name,
        "description": application.description,
        "chat_model_config_id": config.id if config else None,
        "chat_model_revision": config.revision if config else None,
        "system_prompt": application.system_prompt,
        "welcome_message": application.welcome_message,
        "suggested_questions": application.suggested_questions,
        "show_references": application.show_references,
        "agent_enabled": application.agent_enabled,
        "agent_max_steps": application.agent_max_steps,
        "agent_system_prompt": application.agent_system_prompt,
        "enabled_tools": application.enabled_tools,
        "global_top_k": application.global_top_k,
        "max_context_chars": application.max_context_chars,
        "knowledge_bases": [
            {
                "knowledge_base_id": link.knowledge_base_id,
                "position": link.position,
                "weight": link.weight,
                "enabled": link.enabled,
                "retrieval": {
                    "mode": link.knowledge_base.retrieval_mode,
                    "fusion_method": link.knowledge_base.fusion_method,
                    "top_k": link.knowledge_base.retrieval_top_k,
                },
            }
            for link in links
        ],
    }


def publish_application(application, user):
    with transaction.atomic():
        locked = Application.objects.select_for_update().select_related("chat_model_config").get(
            pk=application.pk,
            owner=user,
        )
        links = _validate(locked)
        next_version = (
            locked.versions.aggregate(value=Max("version"))["value"] or 0
        ) + 1
        version = ApplicationVersion.objects.create(
            application=locked,
            version=next_version,
            config_snapshot=build_snapshot(locked, links),
            created_by=user,
        )
        locked.current_published_version = version
        locked.status = Application.Status.PUBLISHED
        locked.save(update_fields=["current_published_version", "status", "updated_at"])
    return version


def rollback_application(application, version, user):
    if version.application_id != application.id:
        raise ApplicationPublishingError("发布版本不存在", "VERSION_NOT_FOUND", 404)
    with transaction.atomic():
        locked = Application.objects.select_for_update().get(pk=application.pk, owner=user)
        locked.current_published_version = version
        locked.status = Application.Status.PUBLISHED
        locked.save(update_fields=["current_published_version", "status", "updated_at"])
    return version
