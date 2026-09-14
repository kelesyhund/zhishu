"""Use the official Prometheus client when installed; keep local dev bootable otherwise."""

try:
    from prometheus_client import (  # type: ignore
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        start_http_server,
    )
except ImportError:  # pragma: no cover - production images install the official dependency.
    import threading
    from collections import defaultdict

    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    _registry = []

    class _MetricValue:
        def __init__(self, metric, labels):
            self.metric = metric
            self.labels_value = labels

        def inc(self, amount=1):
            self.metric.values[self.labels_value] += amount

        def dec(self, amount=1):
            self.metric.values[self.labels_value] -= amount

        def set(self, value):
            self.metric.values[self.labels_value] = value

        def observe(self, value):
            self.metric.values[self.labels_value] += 1
            self.metric.sums[self.labels_value] += float(value)

    class _Metric:
        suffix = ""

        def __init__(self, name, documentation, labelnames=(), **kwargs):
            self.name = name
            self.documentation = documentation
            self.labelnames = tuple(labelnames)
            self.values = defaultdict(float)
            self.sums = defaultdict(float)
            _registry.append(self)

        def labels(self, *values, **kwargs):
            if kwargs:
                values = tuple(kwargs.get(name, "") for name in self.labelnames)
            return _MetricValue(self, tuple(str(value) for value in values))

        def inc(self, amount=1):
            return self.labels().inc(amount)

        def dec(self, amount=1):
            return self.labels().dec(amount)

        def set(self, value):
            return self.labels().set(value)

        def observe(self, value):
            return self.labels().observe(value)

    class Counter(_Metric):
        pass

    class Gauge(_Metric):
        pass

    class Histogram(_Metric):
        pass

    def _labels(metric, values):
        if not values:
            return ""
        pairs = [f'{name}="{str(value).replace(chr(34), "_")}"' for name, value in zip(metric.labelnames, values)]
        return "{" + ",".join(pairs) + "}"

    def generate_latest():
        lines = []
        for metric in _registry:
            lines.append(f"# HELP {metric.name} {metric.documentation}")
            lines.append(f"# TYPE {metric.name} gauge")
            for labels, value in metric.values.items():
                if isinstance(metric, Histogram):
                    lines.append(f"{metric.name}_count{_labels(metric, labels)} {value}")
                    lines.append(f"{metric.name}_sum{_labels(metric, labels)} {metric.sums[labels]}")
                else:
                    lines.append(f"{metric.name}{_labels(metric, labels)} {value}")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def start_http_server(port):
        from wsgiref.simple_server import make_server

        def app(environ, start_response):
            body = generate_latest()
            start_response("200 OK", [("Content-Type", CONTENT_TYPE_LATEST), ("Content-Length", str(len(body)))])
            return [body]

        server = make_server("0.0.0.0", int(port), app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, None
