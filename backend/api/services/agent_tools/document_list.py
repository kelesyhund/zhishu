from api.services.model_resolution import document_needs_reprocess

from .context import ToolContext
from .definitions import JsonObject, ToolResult
from .exceptions import ToolRejectedError


def validate_arguments(arguments: JsonObject) -> JsonObject:
    if arguments:
        raise ToolRejectedError("文档列表工具不接受参数", "TOOL_ARGUMENTS_INVALID")
    return {}


def execute(context: ToolContext, arguments: JsonObject) -> ToolResult:
    documents = list(
        context.knowledge_base.documents.select_related(
            "knowledge_base__embedding_model_config"
        ).order_by("-created_at", "-id")[:50]
    )
    items = [
        {
            "document_id": document.id,
            "name": document.name,
            "status": document.status,
            "paragraph_count": document.paragraph_count,
            "needs_reprocess": document_needs_reprocess(document),
        }
        for document in documents
    ]
    return ToolResult(
        summary=f"当前知识库共有{len(items)}个文档",
        payload={"count": len(items), "items": items},
        model_payload={"count": len(items), "items": items},
    )
