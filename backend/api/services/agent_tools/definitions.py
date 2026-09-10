from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .context import ToolContext


JsonObject = dict[str, Any]
ToolValidator = Callable[[JsonObject], JsonObject]
ToolHandler = Callable[[ToolContext, JsonObject], "ToolResult"]


@dataclass(frozen=True)
class ToolResult:
    summary: str
    payload: JsonObject
    model_payload: JsonObject
    references: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str
    input_schema: JsonObject
    validate_arguments: ToolValidator
    handler: ToolHandler
    allow_public: bool = False
    side_effect_level: str = "NONE"
    requires_owner: bool = True

    def openai_schema(self) -> JsonObject:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
