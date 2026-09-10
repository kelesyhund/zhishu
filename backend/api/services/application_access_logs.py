import hashlib
import uuid
from time import perf_counter

from api.models import ApplicationAccessLog


def client_fingerprint(request):
    value = request.META.get("REMOTE_ADDR", "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def start_access_log(application, version, access_type, request, credential=None):
    log = ApplicationAccessLog.objects.create(
        application=application,
        application_version=version,
        credential=credential,
        request_id=uuid.uuid4().hex,
        access_type=access_type,
        client_fingerprint=client_fingerprint(request),
    )
    return log, perf_counter()


def finish_access_log(
    log,
    started,
    *,
    status=ApplicationAccessLog.Status.SUCCESS,
    status_code=200,
    retrieval_latency_ms=0,
    first_token_latency_ms=0,
    retrieved_paragraph_count=0,
    error_code="",
    conversation=None,
):
    log.status = status
    log.status_code = status_code
    log.retrieval_latency_ms = max(0, retrieval_latency_ms)
    log.first_token_latency_ms = max(0, first_token_latency_ms)
    log.total_latency_ms = max(0, round((perf_counter() - started) * 1000))
    log.model_latency_ms = max(0, log.total_latency_ms - log.retrieval_latency_ms)
    log.retrieved_paragraph_count = max(0, retrieved_paragraph_count)
    log.error_code = error_code[:50]
    log.conversation = conversation
    log.save(
        update_fields=[
            "status",
            "status_code",
            "retrieval_latency_ms",
            "first_token_latency_ms",
            "model_latency_ms",
            "total_latency_ms",
            "retrieved_paragraph_count",
            "error_code",
            "conversation",
        ]
    )
