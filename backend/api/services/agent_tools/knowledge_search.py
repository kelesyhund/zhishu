from typing import Any

from api.services.conversations import public_references
from api.services.retrieval import retrieve_candidates

from .context import ToolContext
from .definitions import JsonObject, ToolResult
from .exceptions import ToolRejectedError


def validate_arguments(arguments: JsonObject) -> JsonObject:
    allowed = {"query", "top_k"}
    if set(arguments) - allowed:
        raise ToolRejectedError("知识检索包含不支持的参数", "TOOL_ARGUMENTS_INVALID")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolRejectedError("知识检索问题不能为空", "TOOL_ARGUMENTS_INVALID")
    query = query.strip()
    if len(query) > 1000:
        raise ToolRejectedError("知识检索问题不能超过1000个字符", "TOOL_ARGUMENTS_INVALID")
    normalized: dict[str, Any] = {"query": query}
    if "top_k" in arguments:
        top_k = arguments["top_k"]
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ToolRejectedError("top_k必须是整数", "TOOL_ARGUMENTS_INVALID")
        if top_k < 1 or top_k > 20:
            raise ToolRejectedError("top_k必须在1到20之间", "TOOL_ARGUMENTS_INVALID")
        normalized["top_k"] = top_k
    return normalized


def execute(context: ToolContext, arguments: JsonObject) -> ToolResult:
    requested_top_k = arguments.get("top_k")
    top_k = min(requested_top_k, context.knowledge_base.retrieval_top_k) if requested_top_k else None
    result = retrieve_candidates(
        context.knowledge_base,
        arguments["query"],
        top_k_override=top_k,
    )
    references = result.references()
    public_items = public_references(references)
    items = [
        {
            "paragraph_id": item.get("paragraph_id"),
            "document_id": item.get("document_id"),
            "document_name": item["document_name"],
            "position": item.get("position", 0),
            "content": item["content"],
            "similarity": item["similarity"],
        }
        for item in public_items
    ]
    count = len(items)
    return ToolResult(
        summary=f"找到{count}条相关资料" if count else "没有找到达到条件的资料",
        payload={"count": count, "items": items},
        model_payload={"count": count, "items": items},
        references=references,
    )
