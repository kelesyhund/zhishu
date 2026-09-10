import math
import threading
import hashlib
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


SAFE_MESSAGES = {
    "NOT_CONFIGURED": "本地重排模型未配置",
    "DEPENDENCY_MISSING": "服务器尚未安装重排模型依赖",
    "MODEL_UNAVAILABLE": "本地重排模型不可用",
    "BUSY": "重排服务繁忙，已使用RRF结果",
    "TIMEOUT": "重排超时，已使用RRF结果",
    "INVALID_OUTPUT": "重排服务返回异常，已使用RRF结果",
    "INFERENCE_FAILED": "重排执行失败，已使用RRF结果",
}


@dataclass(frozen=True)
class RerankOutcome:
    applied: bool
    ordered_ids: list[int]
    scores: dict[int, float]
    fallback_code: str = ""
    fallback_reason: str = ""


_model = None
_model_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knowledge-reranker")
_inference_slot = threading.BoundedSemaphore(1)


def _safe_model_name() -> str:
    value = settings.RERANKER_MODEL_PATH
    if not value:
        return ""
    return Path(value).name if Path(value).exists() else value.rsplit("/", 1)[-1]


@lru_cache(maxsize=4)
def _weight_sha256(model_path: str) -> str | None:
    root = Path(model_path)
    for filename in ("model.safetensors", "pytorch_model.bin"):
        candidate = root / filename
        if candidate.is_file():
            digest = hashlib.sha256()
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
    return None


def reranker_capabilities() -> dict:
    configured = bool(settings.RERANKER_MODEL_PATH)
    local_available = bool(
        configured
        and (settings.RERANKER_ALLOW_DOWNLOAD or Path(settings.RERANKER_MODEL_PATH).exists())
    )
    dependency_available = False
    if configured:
        try:
            import sentence_transformers

            dependency_available = True
            dependency_version = getattr(sentence_transformers, "__version__", "unknown")
        except ImportError:
            dependency_available = False
            dependency_version = None
    else:
        dependency_version = None
    weight_sha256 = (
        _weight_sha256(settings.RERANKER_MODEL_PATH)
        if local_available and Path(settings.RERANKER_MODEL_PATH).is_dir()
        else None
    )
    return {
        "rrf_available": True,
        "reranker_configured": configured,
        "reranker_ready": local_available and dependency_available,
        "reranker_model": _safe_model_name(),
        "reranker_revision": settings.RERANKER_MODEL_REVISION or None,
        "weight_sha256": weight_sha256,
        "sentence_transformers_version": dependency_version,
        "device": settings.RERANKER_DEVICE,
        "batch_size": settings.RERANKER_BATCH_SIZE,
        "max_length": settings.RERANKER_MAX_LENGTH,
        "timeout_seconds": settings.RERANKER_TIMEOUT_SECONDS,
    }


def _load_model():
    global _model
    if _model is not None:
        return _model
    if not settings.RERANKER_MODEL_PATH:
        raise RuntimeError("NOT_CONFIGURED")
    model_path = Path(settings.RERANKER_MODEL_PATH)
    if not model_path.exists() and not settings.RERANKER_ALLOW_DOWNLOAD:
        raise RuntimeError("MODEL_UNAVAILABLE")
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError("DEPENDENCY_MISSING") from exc
    with _model_lock:
        if _model is None:
            try:
                model_options = {
                    "device": settings.RERANKER_DEVICE,
                    "max_length": settings.RERANKER_MAX_LENGTH,
                    "trust_remote_code": False,
                    "local_files_only": not settings.RERANKER_ALLOW_DOWNLOAD,
                }
                if settings.RERANKER_MODEL_REVISION:
                    model_options["revision"] = settings.RERANKER_MODEL_REVISION
                _model = CrossEncoder(settings.RERANKER_MODEL_PATH, **model_options)
            except Exception as exc:
                raise RuntimeError("MODEL_UNAVAILABLE") from exc
    return _model


def _predict(query: str, candidates) -> list[float]:
    model = _load_model()
    pairs = [
        [query, candidate.content[: settings.RERANKER_MAX_CHARS]]
        for candidate in candidates
    ]
    values = model.predict(pairs, batch_size=settings.RERANKER_BATCH_SIZE)
    if hasattr(values, "tolist"):
        values = values.tolist()
    if values and isinstance(values[0], list):
        values = [item[0] for item in values]
    return [float(value) for value in values]


def _fallback(candidates, code: str) -> RerankOutcome:
    safe_code = code if code in SAFE_MESSAGES else "INFERENCE_FAILED"
    return RerankOutcome(
        applied=False,
        ordered_ids=[item.paragraph_id for item in candidates],
        scores={},
        fallback_code=safe_code,
        fallback_reason=SAFE_MESSAGES[safe_code],
    )


def rerank_candidates(query: str, candidates) -> RerankOutcome:
    candidates = list(candidates)
    if not candidates:
        return RerankOutcome(applied=False, ordered_ids=[], scores={})
    if not settings.RERANKER_MODEL_PATH:
        return _fallback(candidates, "NOT_CONFIGURED")
    if not _inference_slot.acquire(blocking=False):
        return _fallback(candidates, "BUSY")
    future = _executor.submit(_predict, query, candidates)
    future.add_done_callback(lambda _future: _inference_slot.release())
    try:
        values = future.result(timeout=settings.RERANKER_TIMEOUT_SECONDS)
    except TimeoutError:
        return _fallback(candidates, "TIMEOUT")
    except RuntimeError as exc:
        return _fallback(candidates, str(exc))
    except Exception:
        return _fallback(candidates, "INFERENCE_FAILED")
    if len(values) != len(candidates) or any(not math.isfinite(value) for value in values):
        return _fallback(candidates, "INVALID_OUTPUT")
    scores = {item.paragraph_id: value for item, value in zip(candidates, values)}
    ordered = sorted(candidates, key=lambda item: (-scores[item.paragraph_id], item.paragraph_id))
    return RerankOutcome(
        applied=True,
        ordered_ids=[item.paragraph_id for item in ordered],
        scores=scores,
    )
