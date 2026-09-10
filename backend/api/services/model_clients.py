from dataclasses import dataclass

import httpx

from api.models import ModelConfig

from .model_crypto import ModelCryptoError, decrypt_api_key
from .model_endpoint_security import EndpointValidationError, validate_model_endpoint


@dataclass(frozen=True)
class ResolvedModelConfig:
    source: str
    model_type: str
    base_url: str
    api_key: str
    model_name: str
    timeout_seconds: int
    config_id: int | None = None
    revision: int | None = None


class ModelServiceError(Exception):
    def __init__(self, message: str, error_code: str = "MODEL_ERROR"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def resolved_database_config(config: ModelConfig) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        source="DATABASE",
        model_type=config.model_type,
        base_url=config.base_url,
        api_key=decrypt_api_key(config.encrypted_api_key),
        model_name=config.model_name,
        timeout_seconds=config.timeout_seconds,
        config_id=config.id,
        revision=config.revision,
    )


def create_openai_client(config: ResolvedModelConfig):
    from openai import OpenAI

    try:
        base_url = validate_model_endpoint(config.base_url) if config.base_url else None
    except EndpointValidationError as exc:
        raise ModelServiceError(str(exc), "UNSAFE_ENDPOINT") from exc
    http_client = httpx.Client(follow_redirects=False, timeout=float(config.timeout_seconds))
    return OpenAI(
        api_key=config.api_key,
        base_url=base_url,
        timeout=float(config.timeout_seconds),
        max_retries=0,
        http_client=http_client,
    )


def map_model_exception(exc: Exception) -> ModelServiceError:
    if isinstance(exc, ModelServiceError):
        return exc
    if isinstance(exc, ModelCryptoError):
        return ModelServiceError(str(exc), "CRYPTO_ERROR")
    if isinstance(exc, EndpointValidationError):
        return ModelServiceError(str(exc), "UNSAFE_ENDPOINT")

    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
    )

    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return ModelServiceError("API Key无效或没有模型权限", "AUTH_FAILED")
    if isinstance(exc, NotFoundError):
        return ModelServiceError("模型地址或模型名称不存在", "NOT_FOUND")
    if isinstance(exc, APITimeoutError):
        return ModelServiceError("模型服务响应超时", "TIMEOUT")
    if isinstance(exc, RateLimitError):
        return ModelServiceError("请求频率过高、额度不足或服务限流", "RATE_LIMITED")
    if isinstance(exc, APIConnectionError):
        return ModelServiceError("无法连接模型服务，请检查地址和网络", "CONNECTION_FAILED")
    if isinstance(exc, APIStatusError):
        if exc.status_code in {401, 403}:
            return ModelServiceError("API Key无效或没有模型权限", "AUTH_FAILED")
        if exc.status_code == 404:
            return ModelServiceError("模型地址或模型名称不存在", "NOT_FOUND")
        if exc.status_code == 408:
            return ModelServiceError("模型服务响应超时", "TIMEOUT")
        if exc.status_code == 429:
            return ModelServiceError("请求频率过高、额度不足或服务限流", "RATE_LIMITED")
    return ModelServiceError("模型服务响应异常，请检查兼容性和配置", "INVALID_RESPONSE")
