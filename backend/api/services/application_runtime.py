from dataclasses import dataclass
from types import SimpleNamespace

from api.models import Application, ApplicationVersion, KnowledgeBase, ModelConfig


class ApplicationRuntimeError(Exception):
    def __init__(self, message, error_code="APPLICATION_RUNTIME_INVALID"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


@dataclass(frozen=True)
class RuntimeKnowledgeBase:
    knowledge_base: KnowledgeBase
    position: int
    weight: float


@dataclass(frozen=True)
class ApplicationRuntime:
    application: Application
    version: ApplicationVersion | None
    name: str
    description: str
    welcome_message: str
    suggested_questions: list[str]
    show_references: bool
    global_top_k: int
    max_context_chars: int
    agent_enabled: bool
    agent_max_steps: int
    agent_system_prompt: str
    enabled_tools: list[str]
    chat_target: object
    knowledge_bases: tuple[RuntimeKnowledgeBase, ...]


def _resolve_chat_config(application, config_id, revision):
    if not config_id:
        return None
    config = ModelConfig.objects.filter(
        pk=config_id,
        owner=application.owner,
        model_type=ModelConfig.ModelType.CHAT,
    ).first()
    if not config:
        raise ApplicationRuntimeError("应用使用的Chat模型配置已不存在", "MODEL_CONFIG_MISSING")
    if revision is not None and config.revision != revision:
        raise ApplicationRuntimeError("模型配置已变化，请重新发布应用", "MODEL_REVISION_CHANGED")
    return config


def _runtime_from_values(application, version, values, knowledge_values):
    config = _resolve_chat_config(
        application,
        values.get("chat_model_config_id"),
        values.get("chat_model_revision"),
    )
    ids = [item["knowledge_base_id"] for item in knowledge_values]
    knowledge_by_id = {
        item.id: item
        for item in KnowledgeBase.objects.filter(
            id__in=ids, workspace=application.workspace
        ).select_related(
            "embedding_model_config"
        )
    }
    if len(knowledge_by_id) != len(set(ids)):
        raise ApplicationRuntimeError("应用关联的知识库已不存在", "KNOWLEDGE_BASE_MISSING")
    links = tuple(
        RuntimeKnowledgeBase(
            knowledge_base=knowledge_by_id[item["knowledge_base_id"]],
            position=item.get("position", index),
            weight=float(item.get("weight", 1)),
        )
        for index, item in enumerate(knowledge_values, start=1)
        if item.get("enabled", True)
    )
    target = SimpleNamespace(
        chat_model_config=config,
        system_prompt=values.get("system_prompt", ""),
        no_answer_message="",
    )
    return ApplicationRuntime(
        application=application,
        version=version,
        name=values.get("name", application.name),
        description=values.get("description", application.description),
        welcome_message=values.get("welcome_message", ""),
        suggested_questions=list(values.get("suggested_questions", [])),
        show_references=bool(values.get("show_references", True)),
        global_top_k=int(values.get("global_top_k", 5)),
        max_context_chars=int(values.get("max_context_chars", 6000)),
        agent_enabled=bool(values.get("agent_enabled", False)),
        agent_max_steps=int(values.get("agent_max_steps", 5)),
        agent_system_prompt=values.get("agent_system_prompt", ""),
        enabled_tools=list(values.get("enabled_tools", [])),
        chat_target=target,
        knowledge_bases=links,
    )


def resolve_draft_runtime(application):
    links = list(application.knowledge_links.filter(enabled=True).order_by("position", "id"))
    values = {
        "name": application.name,
        "description": application.description,
        "chat_model_config_id": application.chat_model_config_id,
        "chat_model_revision": (
            application.chat_model_config.revision if application.chat_model_config_id else None
        ),
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
    }
    knowledge_values = [
        {
            "knowledge_base_id": link.knowledge_base_id,
            "position": link.position,
            "weight": link.weight,
            "enabled": link.enabled,
        }
        for link in links
    ]
    return _runtime_from_values(application, None, values, knowledge_values)


def resolve_published_runtime(application, version=None):
    version = version or application.current_published_version
    if not version or version.application_id != application.id:
        raise ApplicationRuntimeError("应用尚未发布", "APPLICATION_NOT_PUBLISHED")
    snapshot = version.config_snapshot
    return _runtime_from_values(
        application,
        version,
        snapshot,
        snapshot.get("knowledge_bases", []),
    )
