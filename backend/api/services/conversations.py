import re

from django.db import transaction

from api.models import Conversation, KnowledgeBase, Message


DEFAULT_TITLE_LENGTH = 50


def build_default_title(question: str) -> str:
    """把第一条问题压缩为适合列表展示的默认标题。"""

    normalized = re.sub(r"\s+", " ", question).strip()
    return normalized[:DEFAULT_TITLE_LENGTH] or "新对话"


def record_user_question(
    knowledge_base: KnowledgeBase,
    owner,
    question: str,
    conversation: Conversation | None = None,
) -> Conversation:
    """创建新会话或在已有会话中保存用户消息。"""

    conversation, _ = record_user_question_with_message(
        knowledge_base,
        owner,
        question,
        conversation,
    )
    return conversation


def record_user_question_with_message(
    knowledge_base: KnowledgeBase,
    owner,
    question: str,
    conversation: Conversation | None = None,
) -> tuple[Conversation, Message]:
    """保存问题并返回对应消息，供 AgentRun 建立明确关联。"""

    with transaction.atomic():
        if conversation is None:
            conversation = Conversation.objects.create(
                knowledge_base=knowledge_base,
                owner=owner,
                title=build_default_title(question),
            )
        message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=question,
        )
    return conversation, message


def save_assistant_message(
    conversation: Conversation,
    content: str,
    references: list[dict],
) -> Message:
    """只保存完整且非空的 AI 回答。"""

    if not content.strip():
        raise ValueError("模型未返回有效回答")
    return Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=content,
        references=references,
    )


def public_references(references: list[dict]) -> list[dict]:
    public_items = []
    optional_fields = [
        "paragraph_id",
        "document_id",
        "position",
        "vector_score_raw",
        "vector_score_normalized",
        "keyword_score",
        "final_score",
        "knowledge_base_id",
        "knowledge_base_name",
        "application_rrf_score",
        "final_rank",
    ]
    for item in references:
        public_item = {
            "document_name": item["document_name"],
            "content": item["content"][:300],
            "similarity": item["similarity"],
        }
        for field in optional_fields:
            if field in item:
                public_item[field] = item[field]
        public_items.append(public_item)
    return public_items
