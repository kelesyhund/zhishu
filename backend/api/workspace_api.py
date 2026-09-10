from rest_framework.response import Response
from rest_framework.views import APIView

from .services.workspace_permissions import (
    WorkspaceContextError,
    require_capability,
    resolve_workspace_access,
)


class WorkspaceAPIView(APIView):
    """内部 API 的统一工作空间上下文与能力错误外壳。"""

    def workspace_access(self, request):
        access = getattr(request, "workspace_access", None)
        if access is None:
            access = resolve_workspace_access(request)
            request.workspace_access = access
        return access

    def workspace(self, request):
        return self.workspace_access(request).workspace

    def require(self, request, capability):
        return require_capability(request, capability)

    def handle_exception(self, exc):
        if isinstance(exc, WorkspaceContextError):
            return Response(
                {
                    "code": exc.status_code,
                    "message": exc.message,
                    "data": {"error_code": exc.error_code},
                },
                status=exc.status_code,
            )
        return super().handle_exception(exc)

