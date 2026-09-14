import logging
import re
import uuid
from time import perf_counter

from django.urls import Resolver404, resolve

from .telemetry_compat import Status, StatusCode, attach, detach, set_span_in_context

from .observability import (
    HTTP_ERRORS_TOTAL,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
    LOG_CONTEXT,
    REQUEST_ID,
    SSE_CONNECTIONS_ACTIVE,
    SSE_DISCONNECTS_TOTAL,
    SSE_FIRST_TOKEN_DURATION,
    SSE_STREAMS_TOTAL,
    SSE_TOTAL_DURATION,
    current_trace_id,
    tracer,
)


logger = logging.getLogger("api.observability")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id(request) -> str:
    candidate = str(request.headers.get("X-Request-ID", "")).strip()
    return candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else uuid.uuid4().hex


def _route(request) -> str:
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", "") if match else ""
    if not route:
        try:
            route = resolve(request.path_info).route
        except Resolver404:
            return "unresolved"
    return f"/{route}" if not route.startswith("/") else route


def _status_class(status_code: int) -> str:
    return f"{max(1, min(5, int(status_code) // 100))}xx"


class ObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _request_id(request)
        request.request_id = request_id
        request_token = REQUEST_ID.set(request_id)
        initial_log_token = LOG_CONTEXT.set({"request_id": request_id})
        method = request.method.upper()
        route = _route(request)
        request._observability_route = route
        HTTP_REQUESTS_IN_PROGRESS.labels(method, route).inc()
        request._observability_inprogress = True
        started = perf_counter()
        span = tracer().start_span(
            "http.request",
            attributes={"http.request.method": method, "http.route": route},
        )
        span_token = attach(set_span_in_context(span))
        response = None
        try:
            response = self.get_response(request)
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR))
            self._finish(request, method, 500, started, span, response=None)
            raise
        finally:
            detach(span_token)
            LOG_CONTEXT.reset(initial_log_token)
            REQUEST_ID.reset(request_token)

        response["X-Request-ID"] = request_id
        if getattr(response, "streaming", False):
            original = response.streaming_content
            is_sse = str(response.get("Content-Type", "")).startswith("text/event-stream")
            response.streaming_content = self._stream(
                request, response, original, method, route, started, span, request_id, is_sse
            )
            return response

        self._finish(request, method, response.status_code, started, span, response=response)
        return response

    def _stream(self, request, response, original, method, route, started, span, request_id, is_sse):
        def iterator():
            request_token = REQUEST_ID.set(request_id)
            log_token = LOG_CONTEXT.set({"request_id": request_id})
            span_token = attach(set_span_in_context(span))
            first_content = False
            result = "success"
            if is_sse:
                SSE_CONNECTIONS_ACTIVE.labels(route).inc()
            try:
                for chunk in original:
                    if is_sse and not first_content:
                        probe = chunk.decode("utf-8", "ignore") if isinstance(chunk, bytes) else str(chunk)
                        if "event: content" in probe:
                            first_content = True
                            elapsed = max(0, perf_counter() - started)
                            SSE_FIRST_TOKEN_DURATION.labels(route).observe(elapsed)
                    yield chunk
            except GeneratorExit:
                result = "disconnect"
                if is_sse:
                    SSE_DISCONNECTS_TOTAL.labels(route).inc()
                raise
            except Exception as exc:
                result = "failure"
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                if is_sse:
                    SSE_CONNECTIONS_ACTIVE.labels(route).dec()
                    SSE_STREAMS_TOTAL.labels(route, result).inc()
                    SSE_TOTAL_DURATION.labels(route).observe(max(0, perf_counter() - started))
                self._finish(request, method, response.status_code, started, span, response=response)
                detach(span_token)
                LOG_CONTEXT.reset(log_token)
                REQUEST_ID.reset(request_token)

        return iterator()

    def _finish(self, request, method, status_code, started, span, response):
        if not span.is_recording() and getattr(request, "_observability_finished", False):
            return
        if getattr(request, "_observability_finished", False):
            return
        request._observability_finished = True
        route = getattr(request, "_observability_route", _route(request))
        elapsed = max(0, perf_counter() - started)
        status_class = _status_class(status_code)
        HTTP_REQUESTS_TOTAL.labels(method, route, status_class).inc()
        HTTP_REQUEST_DURATION.labels(method, route).observe(elapsed)
        if status_code >= 400:
            HTTP_ERRORS_TOTAL.labels(method, route, status_class).inc()
        if getattr(request, "_observability_inprogress", False):
            HTTP_REQUESTS_IN_PROGRESS.labels(method, route).dec()
        user = getattr(request, "user", None)
        access = getattr(request, "workspace_access", None)
        span_context = span.get_span_context()
        span_trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else current_trace_id()
        extra = {
            "event": "http.request.completed",
            "request_id": getattr(request, "request_id", ""),
            "trace_id": span_trace_id,
            "method": method,
            "route": route,
            "status_code": status_code,
            "duration_ms": round(elapsed * 1000, 2),
            "user_id": getattr(user, "id", None) if getattr(user, "is_authenticated", False) else None,
            "workspace_id": getattr(getattr(access, "workspace", None), "id", None),
            "organization_id": getattr(getattr(getattr(access, "workspace", None), "organization", None), "id", None),
        }
        logger.info("request completed", extra={key: value for key, value in extra.items() if value is not None})
        span.set_attribute("http.route", route)
        span.set_attribute("http.response.status_code", int(status_code))
        if status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))
        span.end()
