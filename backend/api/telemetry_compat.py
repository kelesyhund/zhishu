import contextlib


try:
    from opentelemetry import context as _context, trace as _trace  # type: ignore
    from opentelemetry.propagate import extract, inject  # type: ignore
    from opentelemetry.trace import Status, StatusCode  # type: ignore

    trace = _trace
    attach = _context.attach
    detach = _context.detach
    set_span_in_context = _trace.set_span_in_context
except ImportError:  # pragma: no cover - production images install the official dependency.
    class StatusCode:
        ERROR = "ERROR"

    class Status:
        def __init__(self, status_code):
            self.status_code = status_code

    class _Context:
        is_valid = False
        trace_id = 0

    class _Span:
        def get_span_context(self):
            return _Context()

        def is_recording(self):
            return False

        def set_attribute(self, *args, **kwargs):
            return None

        def set_status(self, *args, **kwargs):
            return None

        def end(self):
            return None

    class _Tracer:
        def start_span(self, *args, **kwargs):
            return _Span()

        @contextlib.contextmanager
        def start_as_current_span(self, *args, **kwargs):
            yield _Span()

    class _Trace:
        _span = _Span()

        @classmethod
        def get_current_span(cls):
            return cls._span

        @staticmethod
        def get_tracer(*args, **kwargs):
            return _Tracer()

    trace = _Trace()

    def attach(context):
        return None

    def detach(token):
        return None

    def set_span_in_context(span):
        return None

    def inject(carrier):
        return None

    def extract(carrier):
        return None
