from dataclasses import dataclass

from django.contrib.auth.models import User

from api.models import AgentRun, Conversation, KnowledgeBase


@dataclass(frozen=True)
class ToolContext:
    user: User | None
    knowledge_base: KnowledgeBase
    conversation: Conversation
    agent_run: AgentRun
    enabled_tools: tuple[str, ...] = ()
    public_access: bool = False
