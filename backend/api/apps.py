from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        from . import signals  # noqa: F401
        from . import worker_observability  # noqa: F401
        from .observability import initialize_tracing

        initialize_tracing()
