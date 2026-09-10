from django.contrib import admin

from .models import Conversation, Document, KnowledgeBase, Message, ModelConfig, Paragraph


admin.site.register([KnowledgeBase, Document, Paragraph, Conversation, Message, ModelConfig])
