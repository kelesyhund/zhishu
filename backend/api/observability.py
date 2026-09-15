import contextlib
import os
from contextvars import ContextVar
from time import perf_counter

from django.conf import settings
from .metrics_compat import Counter, Gauge, Histogram
from .telemetry_compat import Status, StatusCode, trace


REQUEST_ID = ContextVar("request_id", default="")
LOG_CONTEXT = ContextVar("log_context", default={})

HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
LONG_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60, 120, 300, 600)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total", "HTTP requests", ("method", "route", "status_class")
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration", ("method", "route"), buckets=HTTP_BUCKETS
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress", "HTTP requests currently executing", ("method", "route")
)
HTTP_ERRORS_TOTAL = Counter(
    "http_errors_total", "HTTP errors", ("method", "route", "status_class")
)

SSE_CONNECTIONS_ACTIVE = Gauge("sse_connections_active", "Active SSE connections", ("route",))
SSE_STREAMS_TOTAL = Counter("sse_streams_total", "Completed SSE streams", ("route", "result"))
SSE_FIRST_TOKEN_DURATION = Histogram(
    "sse_first_token_duration_seconds", "SSE first content event latency", ("route",), buckets=LONG_BUCKETS
)
SSE_TOTAL_DURATION = Histogram(
    "sse_total_duration_seconds", "SSE stream total duration", ("route",), buckets=LONG_BUCKETS
)
SSE_DISCONNECTS_TOTAL = Counter("sse_disconnects_total", "SSE client disconnects", ("route",))

DOCUMENT_TASKS_TOTAL = Counter("document_tasks_total", "Document task outcomes", ("task_type", "result"))
DOCUMENT_TASK_DURATION = Histogram(
    "document_task_duration_seconds", "Document task execution duration", ("task_type",), buckets=LONG_BUCKETS
)
DOCUMENT_TASKS_IN_PROGRESS = Gauge(
    "document_tasks_in_progress", "Document tasks executing", ("task_type",)
)
DOCUMENT_TASK_RETRIES_TOTAL = Counter(
    "document_task_retries_total", "Document task retries", ("task_type", "error_code")
)
DOCUMENT_TASK_FAILURES_TOTAL = Counter(
    "document_task_failures_total", "Document task failures", ("task_type", "error_code")
)
DOCUMENT_TASK_STALE_TOTAL = Counter(
    "document_task_stale_total", "Document tasks converged as stale", ("task_type", "previous_status")
)
DOCUMENT_TASK_QUEUE_WAIT = Histogram(
    "document_task_queue_wait_seconds", "Document task queue wait", ("task_type",), buckets=LONG_BUCKETS
)

RAG_RETRIEVAL_DURATION = Histogram(
    "rag_retrieval_duration_seconds", "RAG retrieval duration", ("retrieval_strategy",), buckets=LONG_BUCKETS
)
RAG_VECTOR_DURATION = Histogram("rag_vector_duration_seconds", "Vector stage duration", buckets=LONG_BUCKETS)
RAG_BM25_DURATION = Histogram("rag_bm25_duration_seconds", "BM25 stage duration", buckets=LONG_BUCKETS)
RAG_RERANK_DURATION = Histogram("rag_rerank_duration_seconds", "Rerank stage duration", buckets=LONG_BUCKETS)
RAG_CANDIDATES_TOTAL = Counter(
    "rag_candidates_total", "RAG candidates examined", ("retrieval_strategy",)
)
RAG_RERANK_FALLBACKS_TOTAL = Counter(
    "rag_rerank_fallbacks_total", "Reranker fallbacks", ("error_code",)
)
RAG_NO_ANSWER_TOTAL = Counter("rag_no_answer_total", "RAG responses with no evidence")

VECTOR_QUERIES_TOTAL = Counter(
    "vector_queries_total", "Vector query outcomes", ("backend", "mode", "outcome")
)
VECTOR_QUERY_DURATION = Histogram(
    "vector_query_duration_seconds", "Vector query duration", ("backend", "mode"), buckets=LONG_BUCKETS
)
VECTOR_CANDIDATES_TOTAL = Counter(
    "vector_candidates_total", "Vector candidates returned", ("backend",)
)
VECTOR_SHADOW_OVERLAP_RATIO = Histogram(
    "vector_shadow_overlap_ratio", "Legacy and pgvector Top-K overlap ratio", buckets=(0, .25, .5, .75, .9, .95, 1)
)
VECTOR_SHADOW_MISMATCHES_TOTAL = Counter(
    "vector_shadow_mismatches_total", "Vector shadow mismatches", ("reason",)
)
VECTOR_BACKFILL_ROWS_TOTAL = Counter(
    "vector_backfill_rows_total", "Vector backfill row outcomes", ("outcome",)
)
VECTOR_BACKFILL_DURATION = Histogram(
    "vector_backfill_duration_seconds", "Vector backfill batch duration", buckets=LONG_BUCKETS
)
VECTOR_SPACE_COVERAGE_RATIO = Gauge(
    "vector_space_coverage_ratio", "Current workspace vector coverage", ("status",)
)

MODEL_REQUESTS_TOTAL = Counter(
    "model_requests_total", "Model requests", ("model_type", "provider", "result")
)
MODEL_REQUEST_DURATION = Histogram(
    "model_request_duration_seconds", "Model request duration", ("model_type", "provider"), buckets=LONG_BUCKETS
)
MODEL_FIRST_TOKEN_DURATION = Histogram(
    "model_first_token_duration_seconds", "Model first token duration", ("provider",), buckets=LONG_BUCKETS
)
MODEL_TIMEOUTS_TOTAL = Counter("model_timeouts_total", "Model timeouts", ("model_type", "provider"))
MODEL_RATE_LIMITS_TOTAL = Counter("model_rate_limits_total", "Model rate limits", ("model_type", "provider"))
MODEL_FAILURES_TOTAL = Counter(
    "model_failures_total", "Model failures", ("model_type", "provider", "error_code")
)
MODEL_INPUT_TOKENS_TOTAL = Counter(
    "model_input_tokens_total", "Reported model input tokens", ("model_type", "provider")
)
MODEL_OUTPUT_TOKENS_TOTAL = Counter(
    "model_output_tokens_total", "Reported model output tokens", ("model_type", "provider")
)

CELERY_WORKERS_ONLINE = Gauge("celery_workers_online", "Worker process heartbeat", ("worker",))
CELERY_TASKS_ACTIVE = Gauge("celery_tasks_active", "Celery tasks currently active", ("task_name",))
CELERY_TASKS_RETRIED_TOTAL = Counter("celery_tasks_retried_total", "Celery retries", ("task_name",))
CELERY_TASKS_FAILED_TOTAL = Counter("celery_tasks_failed_total", "Celery failures", ("task_name",))
CELERY_QUEUE_DEPTH = Gauge("celery_queue_depth", "Broker queue depth", ("queue",))
CELERY_OLDEST_TASK_AGE = Gauge("celery_oldest_task_age_seconds", "Oldest active database task age")
DEPENDENCY_UP = Gauge("dependency_up", "Required dependency availability", ("dependency",))


def current_request_id() -> str:
    return REQUEST_ID.get()


def current_log_context() -> dict:
    return dict(LOG_CONTEXT.get())


def bind_log_context(**values):
    current = current_log_context()
    current.update({key: value for key, value in values.items() if value not in (None, "")})
    return LOG_CONTEXT.set(current)


def current_trace_id() -> str:
    try:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            return format(context.trace_id, "032x")
    except Exception:
        pass
    return ""


_tracing_initialized = False


def initialize_tracing() -> None:
    global _tracing_initialized
    if _tracing_initialized or not getattr(settings, "OTEL_ENABLED", False):
        return
    endpoint = getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": getattr(settings, "OTEL_SERVICE_NAME", "zhishu-backend"),
                    "deployment.environment": getattr(settings, "ENVIRONMENT", "development"),
                }
            )
        )
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=endpoint.startswith("http://"),
            timeout=getattr(settings, "OTEL_EXPORT_TIMEOUT_SECONDS", 3),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracing_initialized = True
    except Exception:
        # Telemetry is deliberately fail-open: collector/config failures must not break business traffic.
        return


def tracer():
    return trace.get_tracer("zhishu")


@contextlib.contextmanager
def traced(name: str, **attributes):
    safe_attributes = {
        key: value for key, value in attributes.items() if value is not None and isinstance(value, (str, bool, int, float))
    }
    with tracer().start_as_current_span(name, attributes=safe_attributes) as active_span:
        try:
            yield active_span
        except Exception as exc:
            active_span.set_attribute("error.type", type(exc).__name__)
            active_span.set_status(Status(StatusCode.ERROR))
            raise


@contextlib.contextmanager
def timed_metric(histogram, *label_values):
    started = perf_counter()
    try:
        yield
    finally:
        target = histogram.labels(*label_values) if label_values else histogram
        target.observe(max(0, perf_counter() - started))


def normalized_error_code(value: str | None) -> str:
    candidate = str(value or "UNKNOWN").upper()
    allowed = {
        "AUTH_FAILED", "NOT_FOUND", "TIMEOUT", "RATE_LIMITED", "CONNECTION_FAILED",
        "INVALID_RESPONSE", "CRYPTO_ERROR", "UNSAFE_ENDPOINT", "LOCK_BUSY",
        "DATABASE_ERROR", "UNKNOWN", "STALE_TIMEOUT",
    }
    return candidate if candidate in allowed else "OTHER"


def model_provider(source: str | None) -> str:
    candidate = str(source or "LOCAL").upper()
    return candidate if candidate in {"DATABASE", "ENVIRONMENT", "LOCAL"} else "OTHER"


def record_model_failure(model_type: str, provider: str, error_code: str) -> None:
    code = normalized_error_code(error_code)
    normalized_type = model_type if model_type in {"CHAT", "EMBEDDING"} else "OTHER"
    MODEL_REQUESTS_TOTAL.labels(normalized_type, provider, "failure").inc()
    MODEL_FAILURES_TOTAL.labels(normalized_type, provider, code).inc()
    if code == "TIMEOUT":
        MODEL_TIMEOUTS_TOTAL.labels(normalized_type, provider).inc()
    elif code == "RATE_LIMITED":
        MODEL_RATE_LIMITS_TOTAL.labels(normalized_type, provider).inc()


def environment_name() -> str:
    return os.getenv("APP_ENV", "development").strip().lower()
