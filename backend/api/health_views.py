from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from .metrics_compat import CONTENT_TYPE_LATEST, generate_latest
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import DocumentProcessingTask, ModelConfig
from .observability import DEPENDENCY_UP
from .workspace_api import WorkspaceAPIView


def _database_status() -> tuple[bool, str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        # Access a migration-owned key table so an empty but unmigrated database is not considered ready.
        DocumentProcessingTask.objects.only("pk").exists()
        DEPENDENCY_UP.labels("postgresql").set(1)
        return True, "UP"
    except Exception:
        DEPENDENCY_UP.labels("postgresql").set(0)
        return False, "DOWN"


def _redis_status() -> tuple[bool, str]:
    try:
        import redis

        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        DEPENDENCY_UP.labels("redis").set(1)
        return True, "UP"
    except Exception:
        DEPENDENCY_UP.labels("redis").set(0)
        return False, "DOWN"


def _configuration_status() -> tuple[bool, str]:
    valid = not (
        settings.IS_PRODUCTION
        and (
            settings.DEBUG
            or settings.CELERY_TASK_ALWAYS_EAGER
            or settings.SECRET_KEY == "dev-only-change-me"
            or not settings.ALLOWED_HOSTS
            or connection.vendor != "postgresql"
        )
    )
    DEPENDENCY_UP.labels("configuration").set(1 if valid else 0)
    return valid, "UP" if valid else "DOWN"


class LiveHealthView(WorkspaceAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"code": 200, "message": "success", "data": {"status": "UP"}})


class ReadyHealthView(WorkspaceAPIView):
    permission_classes = [AllowAny]

    def get(self, request):
        database_ok, database = _database_status()
        redis_ok, redis_state = _redis_status()
        config_ok, configuration = _configuration_status()
        ready = database_ok and redis_ok and config_ok
        payload = {
            "code": 200 if ready else 503,
            "message": "success" if ready else "service not ready",
            "data": {
                "status": "UP" if ready else "DOWN",
                "checks": {
                    "database": database,
                    "redis": redis_state,
                    "configuration": configuration,
                },
            },
        }
        return Response(payload, status=200 if ready else 503)


class DependencyHealthView(WorkspaceAPIView):
    def get(self, request):
        self.require(request, "audit.read")
        database_ok, database = _database_status()
        redis_ok, redis_state = _redis_status()
        config_ok, configuration = _configuration_status()
        model_state = "DEGRADED" if not database_ok else "UP"
        if database_ok:
            try:
                tested = ModelConfig.objects.filter(workspace=self.workspace(request)).exclude(last_test_status="")
                if tested.filter(last_test_status="FAILURE").exists():
                    model_state = "DEGRADED"
            except Exception:
                model_state = "DEGRADED"
        return Response(
            {
                "code": 200,
                "message": "success",
                "data": {
                    "status": "UP" if database_ok and redis_ok and config_ok else "DOWN",
                    "checks": {
                        "database": database,
                        "redis": redis_state,
                        "configuration": configuration,
                        "models": model_state,
                    },
                },
            }
        )


def prometheus_metrics(request):
    # Refresh only bounded dependency gauges; never expose credentials or raw errors.
    _database_status()
    _redis_status()
    _configuration_status()
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
