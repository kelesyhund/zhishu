import hashlib
import threading
import time

from django.conf import settings


class RateLimitExceeded(Exception):
    def __init__(self, retry_after):
        super().__init__("请求过于频繁，请稍后重试")
        self.message = "请求过于频繁，请稍后重试"
        self.retry_after = max(1, int(retry_after))


_memory_lock = threading.Lock()
_memory_counters = {}


def safe_limit_key(*parts):
    raw = ":".join(str(item) for item in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _memory_increment(key, window_seconds):
    now = int(time.time())
    bucket = now // window_seconds
    with _memory_lock:
        count = _memory_counters.get((key, bucket), 0) + 1
        _memory_counters[(key, bucket)] = count
        if len(_memory_counters) > 5000:
            _memory_counters.clear()
    return count, window_seconds - now % window_seconds


def enforce_rate_limit(key, limit, window_seconds=60):
    backend = getattr(settings, "APPLICATION_RATE_LIMIT_BACKEND", "memory")
    if backend == "redis":
        try:
            import redis

            client = redis.Redis.from_url(settings.APPLICATION_RATE_LIMIT_REDIS_URL)
            bucket = int(time.time()) // window_seconds
            redis_key = f"app-limit:{safe_limit_key(key, bucket)}"
            with client.pipeline(transaction=True) as pipeline:
                pipeline.incr(redis_key)
                pipeline.expire(redis_key, window_seconds + 1)
                count, _ = pipeline.execute()
            retry_after = window_seconds - int(time.time()) % window_seconds
        except Exception as exc:
            raise RuntimeError("访问保护服务暂不可用，请稍后重试") from exc
    else:
        count, retry_after = _memory_increment(safe_limit_key(key), window_seconds)
    if count > limit:
        raise RateLimitExceeded(retry_after)


def reset_memory_rate_limits():
    with _memory_lock:
        _memory_counters.clear()
