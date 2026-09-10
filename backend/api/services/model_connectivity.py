from time import monotonic

from django.utils import timezone

from api.models import ModelConfig

from .model_clients import create_openai_client, map_model_exception, resolved_database_config


def test_model_config(config: ModelConfig) -> dict:
    started = monotonic()
    try:
        resolved = resolved_database_config(config)
        with create_openai_client(resolved) as client:
            if config.model_type == ModelConfig.ModelType.CHAT:
                response = client.chat.completions.create(
                    model=config.model_name,
                    messages=[{"role": "user", "content": "只回复OK"}],
                    max_tokens=8,
                    stream=False,
                )
                if not response.choices:
                    raise ValueError("empty choices")
                dimension = None
            else:
                response = client.embeddings.create(
                    model=config.model_name,
                    input=["Knowledge Chat connectivity test"],
                )
                if not response.data or not response.data[0].embedding:
                    raise ValueError("empty embedding")
                vector = response.data[0].embedding
                if not all(isinstance(value, (int, float)) for value in vector):
                    raise ValueError("invalid embedding")
                dimension = len(vector)
    except Exception as exc:
        safe_error = map_model_exception(exc)
        config.last_test_status = ModelConfig.TestStatus.FAILURE
        config.last_test_message = safe_error.message[:200]
        config.last_test_at = timezone.now()
        config.save(update_fields=["last_test_status", "last_test_message", "last_test_at", "updated_at"])
        raise safe_error from exc

    latency_ms = max(1, round((monotonic() - started) * 1000))
    config.last_test_status = ModelConfig.TestStatus.SUCCESS
    config.last_test_message = "模型连接成功"
    config.last_test_at = timezone.now()
    if dimension is not None:
        config.embedding_dimension = dimension
    config.save(
        update_fields=[
            "last_test_status",
            "last_test_message",
            "last_test_at",
            "embedding_dimension",
            "updated_at",
        ]
    )
    return {
        "model_type": config.model_type,
        "success": True,
        "latency_ms": latency_ms,
        "embedding_dimension": dimension,
        "message": "模型连接成功",
    }
