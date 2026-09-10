from django.db import transaction

from api.models import Conversation, Message

from .conversations import build_default_title


def get_application_conversation(
    application,
    conversation_id,
    *,
    owner=None,
    visitor_id_hash="",
    access_type=None,
):
    try:
        normalized = int(conversation_id)
    except (TypeError, ValueError):
        return None
    queryset = Conversation.objects.filter(pk=normalized, application=application)
    if owner is not None:
        queryset = queryset.filter(owner=owner, visitor_id_hash="")
    else:
        queryset = queryset.filter(owner__isnull=True, visitor_id_hash=visitor_id_hash)
    if access_type:
        queryset = queryset.filter(access_type=access_type)
    return queryset.first()


def record_application_question(
    runtime,
    question,
    *,
    conversation=None,
    owner=None,
    visitor_id_hash="",
    access_type,
):
    with transaction.atomic():
        if conversation is None:
            conversation = Conversation.objects.create(
                application=runtime.application,
                application_version=runtime.version,
                owner=owner,
                visitor_id_hash=visitor_id_hash,
                access_type=access_type,
                title=build_default_title(question),
            )
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=question,
        )
        conversation.save(update_fields=["updated_at"])
    return conversation, user_message
