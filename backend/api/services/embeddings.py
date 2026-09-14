import hashlib
import math
from time import perf_counter

from api.models import KnowledgeBase

from .model_clients import create_openai_client, map_model_exception
from .model_resolution import resolve_embedding_config
from .retrieval_tokenizer import tokenize_text
from ..observability import (
    MODEL_REQUEST_DURATION,
    MODEL_REQUESTS_TOTAL,
    model_provider,
    record_model_failure,
    traced,
)


VECTOR_SIZE = 256


def local_embedding(text: str) -> list[float]:
    vector = [0.0] * VECTOR_SIZE
    for token in tokenize_text(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % VECTOR_SIZE
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def embed_texts(texts: list[str], knowledge_base: KnowledgeBase | None = None) -> list[list[float]]:
    config = resolve_embedding_config(knowledge_base) if knowledge_base else None
    if config:
        provider = model_provider(getattr(config, "source", "DATABASE"))
        started = perf_counter()
        try:
            with traced(
                "embedding.batch",
                model_type="EMBEDDING",
                provider=provider,
                selected_count=len(texts),
            ), create_openai_client(config) as client:
                response = client.embeddings.create(model=config.model_name, input=texts)
            vectors = [item.embedding for item in response.data]
            if len(vectors) != len(texts) or any(not vector for vector in vectors):
                raise ValueError("invalid embedding response")
            MODEL_REQUESTS_TOTAL.labels("EMBEDDING", provider, "success").inc()
            return vectors
        except Exception as exc:
            mapped = map_model_exception(exc)
            record_model_failure("EMBEDDING", provider, mapped.error_code)
            raise mapped from exc
        finally:
            MODEL_REQUEST_DURATION.labels("EMBEDDING", provider).observe(max(0, perf_counter() - started))
    return [local_embedding(text) for text in texts]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        raise ValueError("向量维度不一致")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    score = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, score))
