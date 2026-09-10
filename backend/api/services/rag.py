import json
from collections.abc import Iterator

from api.models import KnowledgeBase

from .embeddings import embed_texts
from .model_clients import ModelServiceError, create_openai_client, map_model_exception
from .model_resolution import resolve_chat_config
from .prompt_builder import build_no_answer_message, build_rag_messages
from .retrieval import retrieve_candidates


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
        message = build_no_answer_message(knowledge_base)
        for index in range(0, len(message), 16):
            yield message[index : index + 16]
        return

    config = resolve_chat_config(knowledge_base)
    if config:
        try:
            with create_openai_client(config) as client:
                stream = client.chat.completions.create(
                    model=config.model_name,
                    messages=build_rag_messages(knowledge_base, question, references),
                    stream=True,
                )
                for chunk in stream:
                    content = chunk.choices[0].delta.content or ""
                    if content:
                        yield content
        except Exception as exc:
            raise map_model_exception(exc) from exc
        return

    best = references[0]
    demo_answer = (
        "当前处于本地演示模式，尚未配置大模型。根据检索到的资料，最相关内容如下：\n\n"
        f"{best['content'][:600]}\n\n[资料1：{best['document_name']}]"
    )
    for index in range(0, len(demo_answer), 16):
        yield demo_answer[index : index + 16]


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
