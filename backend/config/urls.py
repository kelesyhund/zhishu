from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from api.health_views import (
    DependencyHealthView,
    LiveHealthView,
    ReadyHealthView,
    prometheus_metrics,
)
from api.media_views import ProtectedMediaView


urlpatterns = [
    path("health/live/", LiveHealthView.as_view()),
    path("health/ready/", ReadyHealthView.as_view()),
    path("health/dependencies/", DependencyHealthView.as_view()),
    path("internal/metrics/", prometheus_metrics),
    path("media/<path:file_path>", ProtectedMediaView.as_view()),
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
