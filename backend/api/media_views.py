import mimetypes
from pathlib import PurePosixPath
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document
from .services.workspace_permissions import (
    CAPABILITIES_BY_ROLE,
    accessible_workspaces,
    effective_workspace_role,
)


class ProtectedMediaView(APIView):
    """Authorize a document file before Nginx performs the actual disk transfer."""

    def get(self, request, file_path):
        normalized = PurePosixPath(str(file_path).replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            return Response({"code": 404, "message": "文件不存在", "data": None}, status=404)
        relative = normalized.as_posix().lstrip("/")
        document = (
            Document.objects.filter(
                file=relative,
                knowledge_base__workspace__in=accessible_workspaces(request.user),
            )
            .select_related("knowledge_base__workspace__organization")
            .first()
        )
        if not document:
            return Response({"code": 404, "message": "文件不存在", "data": None}, status=404)
        role = effective_workspace_role(request.user, document.knowledge_base.workspace)
        if "knowledge.read" not in CAPABILITIES_BY_ROLE.get(role, set()):
            return Response({"code": 404, "message": "文件不存在", "data": None}, status=404)
        try:
            absolute = document.file.path
        except (NotImplementedError, ValueError):
            return Response({"code": 404, "message": "文件不存在", "data": None}, status=404)
        content_type = mimetypes.guess_type(document.name)[0] or "application/octet-stream"
        if settings.DEBUG:
            try:
                return FileResponse(open(absolute, "rb"), content_type=content_type, filename=document.name)
            except OSError:
                return Response({"code": 404, "message": "文件不存在", "data": None}, status=404)
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = f"/_protected_media/{quote(relative, safe='/')}"
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(document.name)}"
        response["Cache-Control"] = "private, no-store"
        return response
