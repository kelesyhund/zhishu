import threading
import uuid
from contextlib import contextmanager

from django.conf import settings


class DocumentTaskLockError(Exception):
    pass


class DocumentTaskLockBusy(DocumentTaskLockError):
    pass


_memory_guard = threading.Lock()
_memory_tokens: dict[int, str] = {}


@contextmanager
def _memory_lock(document_id: int):
    token = uuid.uuid4().hex
    with _memory_guard:
        if document_id in _memory_tokens:
            raise DocumentTaskLockBusy("文档已有处理任务正在执行")
        _memory_tokens[document_id] = token
    try:
        yield
    finally:
        with _memory_guard:
            if _memory_tokens.get(document_id) == token:
                _memory_tokens.pop(document_id, None)


@contextmanager
def _redis_lock(document_id: int):
    import redis
    from redis.exceptions import LockError, RedisError

    client = redis.Redis.from_url(
        settings.DOCUMENT_TASK_LOCK_REDIS_URL,
        socket_connect_timeout=3,
        socket_timeout=3,
        decode_responses=True,
    )
    lock = client.lock(
        f"knowledge-chat:document-processing:{document_id}",
        timeout=settings.DOCUMENT_TASK_LOCK_TIMEOUT,
        blocking=False,
        thread_local=False,
    )
    try:
        acquired = lock.acquire(blocking=False)
    except RedisError as exc:
        raise DocumentTaskLockError("任务协调服务暂时不可用") from exc
    if not acquired:
        raise DocumentTaskLockBusy("文档已有处理任务正在执行")
    try:
        yield
    finally:
        try:
            lock.release()
        except LockError:
            pass


def document_processing_lock(document_id: int):
    if settings.DOCUMENT_TASK_LOCK_BACKEND == "memory":
        return _memory_lock(document_id)
    return _redis_lock(document_id)
