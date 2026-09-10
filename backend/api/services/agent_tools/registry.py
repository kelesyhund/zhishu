from . import calculator, document_list, knowledge_search
from .definitions import ToolDefinition


TOOL_DEFINITIONS = [
    ToolDefinition(
        name="knowledge_search",
        label="知识检索",
        description="检索当前知识库中与问题相关的安全文档切片。不要传入知识库ID。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要检索的问题或关键词"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        validate_arguments=knowledge_search.validate_arguments,
        handler=knowledge_search.execute,
        allow_public=True,
        requires_owner=False,
    ),
    ToolDefinition(
        name="document_list",
        label="文档列表",
        description="列出当前知识库中的文档状态、切片数量和是否需要重新处理。",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        validate_arguments=document_list.validate_arguments,
        handler=document_list.execute,
        allow_public=True,
        requires_owner=False,
    ),
    ToolDefinition(
        name="calculator",
        label="安全计算器",
        description="计算只包含数字、括号和基本算术运算符的表达式。",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "安全算术表达式"},
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        validate_arguments=calculator.validate_arguments,
        handler=calculator.execute,
        allow_public=True,
        requires_owner=False,
    ),
]
TOOL_REGISTRY = {definition.name: definition for definition in TOOL_DEFINITIONS}


def get_tool(name: str) -> ToolDefinition | None:
    return TOOL_REGISTRY.get(name)


def get_tool_schemas(enabled_tools: list[str]) -> list[dict]:
    enabled = set(enabled_tools)
    return [tool.openai_schema() for tool in TOOL_DEFINITIONS if tool.name in enabled]


def available_tools_metadata() -> list[dict]:
    return [
        {
            "name": tool.name,
            "label": tool.label,
            "description": tool.description,
            "allow_public": tool.allow_public,
            "side_effect_level": tool.side_effect_level,
        }
        for tool in TOOL_DEFINITIONS
    ]
