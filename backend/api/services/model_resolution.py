import os

from api.models import Document, KnowledgeBase, ModelConfig

from .model_clients import ResolvedModelConfig, resolved_database_config


def resolve_chat_config(knowledge_base: KnowledgeBase) -> ResolvedModelConfig | None:
    config = knowledge_base.chat_model_config
    if config:
        return resolved_database_config(config)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model_name = os.getenv("LLM_MODEL", "").strip()
    if api_key and model_name:
        return ResolvedModelConfig(
            source="ENVIRONMENT",
            model_type=ModelConfig.ModelType.CHAT,
            base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=30,
        )
    return None


def resolve_embedding_config(knowledge_base: KnowledgeBase) -> ResolvedModelConfig | None:
    config = knowledge_base.embedding_model_config
    if config:
        return resolved_database_config(config)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model_name = os.getenv("EMBEDDING_MODEL", "").strip()
    if api_key and model_name:
        return ResolvedModelConfig(
            source="ENVIRONMENT",
            model_type=ModelConfig.ModelType.EMBEDDING,
            base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=30,
        )
    return None


def active_embedding_signature(knowledge_base: KnowledgeBase) -> str:
    config = knowledge_base.embedding_model_config
    return f"config:{config.id}:revision:{config.revision}" if config else ""


def document_needs_reprocess(document: Document) -> bool:
    from .document_chunking import chunking_signature

    if document.status != Document.Status.SUCCESS:
        return False
    signature = active_embedding_signature(document.knowledge_base)
    embedding_stale = bool(signature and document.embedding_signature != signature)
    current_chunking_signature = chunking_signature(document)
    chunking_stale = bool(
        (
            document.indexed_chunking_signature
            and document.indexed_chunking_signature != current_chunking_signature
        )
        or (
            document.chunk_strategy != Document.ChunkStrategy.LEGACY
            and not document.indexed_chunking_signature
        )
    )
    return embedding_stale or chunking_stale


def model_status(knowledge_base: KnowledgeBase) -> dict:
    chat = knowledge_base.chat_model_config
    embedding = knowledge_base.embedding_model_config
    env_chat = bool(os.getenv("OPENAI_API_KEY", "").strip() and os.getenv("LLM_MODEL", "").strip())
    env_embedding = bool(
        os.getenv("OPENAI_API_KEY", "").strip() and os.getenv("EMBEDDING_MODEL", "").strip()
    )

    if chat:
        chat_status = {
            "source": "DATABASE",
            "config_id": chat.id,
            "label": chat.name,
            "model_name": chat.model_name,
        }
    elif env_chat:
        chat_status = {
            "source": "ENVIRONMENT",
            "config_id": None,
            "label": "系统默认模型",
            "model_name": os.getenv("LLM_MODEL", "").strip(),
        }
    else:
        chat_status = {
            "source": "LOCAL",
            "config_id": None,
            "label": "本地演示模式",
            "model_name": "",
        }

    if embedding:
        embedding_status = {
            "source": "DATABASE",
            "config_id": embedding.id,
            "label": embedding.name,
            "model_name": embedding.model_name,
        }
    elif env_embedding:
        embedding_status = {
            "source": "ENVIRONMENT",
            "config_id": None,
            "label": "系统默认向量模型",
            "model_name": os.getenv("EMBEDDING_MODEL", "").strip(),
        }
    else:
        embedding_status = {
            "source": "LOCAL",
            "config_id": None,
            "label": "本地哈希向量",
            "model_name": "local-hash-256",
        }

    stale_count = 0
    if embedding:
        signature = active_embedding_signature(knowledge_base)
        stale_count = knowledge_base.documents.exclude(embedding_signature=signature).count()
    return {
        "chat_model_config_id": chat.id if chat else None,
        "embedding_model_config_id": embedding.id if embedding else None,
        "chat": chat_status,
        "embedding": embedding_status,
        "embedding_stale_document_count": stale_count,
    }
