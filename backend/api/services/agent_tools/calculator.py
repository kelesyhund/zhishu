import ast
import math
import operator

from .context import ToolContext
from .definitions import JsonObject, ToolResult
from .exceptions import ToolRejectedError


MAX_EXPRESSION_LENGTH = 200
MAX_AST_NODES = 80
MAX_EXPONENT = 12
MAX_ABSOLUTE_RESULT = 1e100

BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def validate_arguments(arguments: JsonObject) -> JsonObject:
    if set(arguments) != {"expression"}:
        raise ToolRejectedError("计算器只接受expression参数", "TOOL_ARGUMENTS_INVALID")
    expression = arguments.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        raise ToolRejectedError("计算表达式不能为空", "TOOL_ARGUMENTS_INVALID")
    expression = expression.strip()
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ToolRejectedError("计算表达式过长", "TOOL_ARGUMENTS_INVALID")
    return {"expression": expression}


def _check_result(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolRejectedError("计算表达式包含不支持的值", "TOOL_ARGUMENTS_INVALID")
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolRejectedError("计算结果不是有限数字", "TOOL_EXECUTION_FAILED")
    if abs(value) > MAX_ABSOLUTE_RESULT:
        raise ToolRejectedError("计算结果过大", "TOOL_EXECUTION_FAILED")
    return value


def _evaluate(node):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or type(node.value) not in {int, float}:
            raise ToolRejectedError("计算器只支持数字", "TOOL_ARGUMENTS_INVALID")
        return _check_result(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPERATORS:
        return _check_result(UNARY_OPERATORS[type(node.op)](_evaluate(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ToolRejectedError("幂指数过大", "TOOL_EXECUTION_FAILED")
        try:
            return _check_result(BINARY_OPERATORS[type(node.op)](left, right))
        except ZeroDivisionError as exc:
            raise ToolRejectedError("不能除以零", "TOOL_EXECUTION_FAILED") from exc
        except (OverflowError, ValueError) as exc:
            raise ToolRejectedError("计算结果超出安全范围", "TOOL_EXECUTION_FAILED") from exc
    raise ToolRejectedError("计算表达式包含不允许的语法", "TOOL_ARGUMENTS_INVALID")


def calculate(expression: str):
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolRejectedError("计算表达式格式无效", "TOOL_ARGUMENTS_INVALID") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise ToolRejectedError("计算表达式过于复杂", "TOOL_ARGUMENTS_INVALID")
    return _evaluate(tree)


def _display_number(value) -> str:
    if isinstance(value, int):
        return str(value)
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


def execute(context: ToolContext, arguments: JsonObject) -> ToolResult:
    value = calculate(arguments["expression"])
    display = _display_number(value)
    payload = {"expression": arguments["expression"], "result": value, "display": display}
    return ToolResult(
        summary=f"计算结果：{display}",
        payload=payload,
        model_payload=payload,
    )
