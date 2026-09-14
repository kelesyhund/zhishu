import json
from collections.abc import Iterator
from time import perf_counter

from api.models import KnowledgeBase

from .embeddings import embed_texts
from .model_clients import ModelServiceError, create_openai_client, map_model_exception
from .model_resolution import resolve_chat_config
from .prompt_builder import build_no_answer_message, build_rag_messages
from .retrieval import retrieve_candidates
from ..observability import (
    MODEL_FIRST_TOKEN_DURATION,
    MODEL_REQUEST_DURATION,
    MODEL_REQUESTS_TOTAL,
    RAG_NO_ANSWER_TOTAL,
    model_provider,
    record_model_failure,
    traced,
)


def search_paragraphs(
    knowledge_base: KnowledgeBase,
    query: str,
    top_k: int | None = None,
) -> list[dict]:
    return retrieve_candidates(
        knowledge_base,
        query,
        top_k_override=top_k,
        embedding_function=embed_texts,
    ).references()


def stream_answer(
    knowledge_base: KnowledgeBase,
    question: str,
    references: list[dict],
) -> Iterator[str]:
    if not references:
        RAG_NO_ANSWER_TOTAL.inc()
        message = build_no_answer_message(knowledge_base)
        for index in range(0, len(message), 16):
            yield message[index : index + 16]
        return

    config = resolve_chat_config(knowledge_base)
    if config:
        provider = model_provider(getattr(config, "source", "DATABASE"))
        started = perf_counter()
        first_token = False
        try:
            with traced("context.build", selected_count=len(references)):
                messages = build_rag_messages(knowledge_base, question, references)
            with traced("llm.chat", model_type="CHAT", provider=provider), create_openai_client(config) as client:
                stream = client.chat.completions.create(
                    model=config.model_name,
                    messages=messages,
                    stream=True,
                )
                for chunk in stream:
                    content = chunk.choices[0].delta.content or ""
                    if content:
                        if not first_token:
                            first_token = True
                            MODEL_FIRST_TOKEN_DURATION.labels(provider).observe(max(0, perf_counter() - started))
                        yield content
            MODEL_REQUESTS_TOTAL.labels("CHAT", provider, "success").inc()
        except Exception as exc:
            mapped = map_model_exception(exc)
            record_model_failure("CHAT", provider, mapped.error_code)
            raise mapped from exc
        finally:
            MODEL_REQUEST_DURATION.labels("CHAT", provider).observe(max(0, perf_counter() - started))
        return

    best = references[0]
    fallback_answer = (
        "当前未配置生成模型，系统已切换为证据检索模式。根据知识库资料，最相关内容如下：\n\n"
        f"{best['content'][:600]}\n\n[资料1：{best['document_name']}]"
    )
    for index in range(0, len(fallback_answer), 16):
        yield fallback_answer[index : index + 16]


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
