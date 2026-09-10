import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

from api.models import AgentRun, Message, ToolExecution

from .agent_persistence import (
    create_tool_execution,
    finish_agent_run,
    finish_tool_execution,
)
from .agent_prompts import resolve_agent_system_prompt
from .agent_tools.context import ToolContext
from .agent_tools.exceptions import ToolError, ToolRejectedError
from .agent_tools.registry import get_tool, get_tool_schemas
from .conversations import public_references, save_assistant_message
from .model_clients import create_openai_client, map_model_exception
from .model_resolution import resolve_chat_config


MAX_TOOL_CALLS_PER_STEP = 3
MAX_TOOL_CALLS_PER_RUN = 10
MAX_TOOL_ARGUMENT_CHARS = 4000
MAX_TOOL_RESULT_CHARS = 6000
MAX_TOTAL_TOOL_RESULT_CHARS = 12000
MAX_AGENT_RUNTIME_SECONDS = 120
MAX_HISTORY_MESSAGES = 20


class AgentExecutionError(Exception):
    def __init__(self, message: str, error_code: str, *, status: str = AgentRun.Status.FAILURE):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status = status


@dataclass
class AccumulatedToolCall:
    index: int
    tool_call_id: str = ""
    name: str = ""
    arguments: str = ""

    def openai_message_value(self) -> dict:
        return {
            "id": self.tool_call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


def _read(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _safe_agent_model_error(exc: Exception) -> AgentExecutionError:
    raw = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code == 400 and any(word in raw for word in ("tool", "function")):
        return AgentExecutionError(
            "当前Chat模型或服务不支持Agent工具调用，请更换兼容模型或关闭Agent模式。",
            "TOOL_CALLING_UNSUPPORTED",
        )
    safe = map_model_exception(exc)
    if safe.error_code == "INVALID_RESPONSE" and any(
        word in raw for word in ("tool", "function", "unsupported")
    ):
        return AgentExecutionError(
            "当前Chat模型或服务不支持Agent工具调用，请更换兼容模型或关闭Agent模式。",
            "TOOL_CALLING_UNSUPPORTED",
        )
    return AgentExecutionError(safe.message, safe.error_code)


def _collect_model_turn(stream) -> tuple[list[str], list[AccumulatedToolCall]]:
    content_parts: list[str] = []
    calls: dict[int, AccumulatedToolCall] = {}
    for chunk in stream:
        choices = _read(chunk, "choices", []) or []
        if not choices:
            continue
        delta = _read(choices[0], "delta")
        if delta is None:
            continue
        content = _read(delta, "content")
        if isinstance(content, str) and content:
            content_parts.append(content)
        for item in _read(delta, "tool_calls", []) or []:
            index = _read(item, "index")
            if not isinstance(index, int) or index < 0:
                raise AgentExecutionError("模型返回了无效的工具调用协议", "AGENT_INVALID_RESPONSE")
            call = calls.setdefault(index, AccumulatedToolCall(index=index))
            tool_call_id = _read(item, "id")
            if isinstance(tool_call_id, str):
                call.tool_call_id += tool_call_id
            function = _read(item, "function")
            if function is not None:
                name = _read(function, "name")
                arguments = _read(function, "arguments")
                if isinstance(name, str):
                    call.name += name
                if isinstance(arguments, str):
                    call.arguments += arguments
                    if len(call.arguments) > MAX_TOOL_ARGUMENT_CHARS:
                        raise AgentExecutionError("工具参数超过安全长度限制", "TOOL_ARGUMENTS_INVALID")
    ordered = [calls[index] for index in sorted(calls)]
    for call in ordered:
        if not call.tool_call_id:
            call.tool_call_id = f"tool-call-{call.index}"
    return content_parts, ordered


def _initial_messages(agent_run: AgentRun, prompt_target) -> list[dict]:
    history = list(
        agent_run.conversation.messages.filter(id__lt=agent_run.user_message_id)
        .order_by("-created_at", "-id")[:MAX_HISTORY_MESSAGES]
    )
    history.reverse()
    messages = [{"role": "system", "content": resolve_agent_system_prompt(prompt_target)}]
    messages.extend({"role": message.role, "content": message.content} for message in history)
    messages.append({"role": "user", "content": agent_run.user_message.content})
    return messages


def _deduplicate_references(references: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in references:
        key = item.get("paragraph_id") or (
            item.get("document_id"),
            item.get("position"),
            item.get("content"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _agent_event(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


def _prepare_tool(
    context: ToolContext,
    call: AccumulatedToolCall,
    *,
    step: int,
    sequence: int,
) -> tuple[Any, dict, ToolExecution]:
    definition = get_tool(call.name)
    if definition is None:
        execution = create_tool_execution(
            context.agent_run,
            step=step,
            sequence=sequence,
            tool_call_id=call.tool_call_id,
            tool_name=call.name or "unknown",
            arguments={},
        )
        finish_tool_execution(
            execution,
            ToolExecution.Status.REJECTED,
            latency_ms=0,
            error_code="TOOL_NOT_FOUND",
            error_message="模型请求了未注册工具",
        )
        raise ToolRejectedError("模型请求了未注册工具", "TOOL_NOT_FOUND")
    if definition.name not in context.enabled_tools:
        execution = create_tool_execution(
            context.agent_run,
            step=step,
            sequence=sequence,
            tool_call_id=call.tool_call_id,
            tool_name=definition.name,
            arguments={},
        )
        finish_tool_execution(
            execution,
            ToolExecution.Status.REJECTED,
            latency_ms=0,
            error_code="TOOL_NOT_ALLOWED",
            error_message="当前知识库未启用该工具",
        )
        raise ToolRejectedError("当前知识库未启用该工具", "TOOL_NOT_ALLOWED")
    if context.public_access and (
        not definition.allow_public
        or definition.requires_owner
        or definition.side_effect_level != "NONE"
    ):
        execution = create_tool_execution(
            context.agent_run,
            step=step,
            sequence=sequence,
            tool_call_id=call.tool_call_id,
            tool_name=definition.name,
            arguments={},
        )
        finish_tool_execution(
            execution,
            ToolExecution.Status.REJECTED,
            latency_ms=0,
            error_code="PUBLIC_TOOL_NOT_ALLOWED",
            error_message="公开应用不允许调用该工具",
        )
        raise ToolRejectedError("公开应用不允许调用该工具", "PUBLIC_TOOL_NOT_ALLOWED")
    try:
        parsed = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        parsed = None
        validation_error = ToolRejectedError("工具参数不是有效JSON", "TOOL_ARGUMENTS_INVALID")
    else:
        validation_error = None
    if not isinstance(parsed, dict):
        validation_error = validation_error or ToolRejectedError(
            "工具参数必须是JSON对象", "TOOL_ARGUMENTS_INVALID"
        )
    if validation_error:
        execution = create_tool_execution(
            context.agent_run,
            step=step,
            sequence=sequence,
            tool_call_id=call.tool_call_id,
            tool_name=definition.name,
            arguments={},
        )
        finish_tool_execution(
            execution,
            ToolExecution.Status.REJECTED,
            latency_ms=0,
            error_code=validation_error.error_code,
            error_message=validation_error.message,
        )
        raise validation_error
    try:
        arguments = definition.validate_arguments(parsed)
    except ToolError as exc:
        execution = create_tool_execution(
            context.agent_run,
            step=step,
            sequence=sequence,
            tool_call_id=call.tool_call_id,
            tool_name=definition.name,
            arguments={},
        )
        finish_tool_execution(
            execution,
            ToolExecution.Status.REJECTED,
            latency_ms=0,
            error_code=exc.error_code,
            error_message=exc.message,
        )
        raise
    execution = create_tool_execution(
        context.agent_run,
        step=step,
        sequence=sequence,
        tool_call_id=call.tool_call_id,
        tool_name=definition.name,
        arguments=arguments,
    )
    return definition, arguments, execution


def _execute_tool(
    context: ToolContext,
    definition,
    arguments: dict,
    execution: ToolExecution,
) -> tuple[Any, int]:
    started = monotonic()
    try:
        result = definition.handler(context, arguments)
        model_json = json.dumps(result.model_payload, ensure_ascii=False, separators=(",", ":"))
        if len(model_json) > MAX_TOOL_RESULT_CHARS:
            raise ToolError("工具结果超过安全长度限制", "TOOL_RESULT_TOO_LARGE")
    except ToolError as exc:
        latency_ms = max(0, round((monotonic() - started) * 1000))
        status = (
            ToolExecution.Status.REJECTED
            if isinstance(exc, ToolRejectedError)
            else ToolExecution.Status.FAILURE
        )
        finish_tool_execution(
            execution,
            status,
            latency_ms=latency_ms,
            error_code=exc.error_code,
            error_message=exc.message,
        )
        raise
    except Exception as exc:
        latency_ms = max(0, round((monotonic() - started) * 1000))
        finish_tool_execution(
            execution,
            ToolExecution.Status.FAILURE,
            latency_ms=latency_ms,
            error_code="TOOL_EXECUTION_FAILED",
            error_message="工具执行失败，请稍后重试",
        )
        raise ToolError("工具执行失败，请稍后重试", "TOOL_EXECUTION_FAILED") from exc
    latency_ms = max(0, round((monotonic() - started) * 1000))
    finish_tool_execution(
        execution,
        ToolExecution.Status.SUCCESS,
        latency_ms=latency_ms,
        result_summary=result.summary,
        result_payload=result.payload,
    )
    return result, len(model_json)


def stream_agent_run(agent_run: AgentRun):
    step_count = 0
    terminal = False
    started = monotonic()
    try:
        conversation = agent_run.conversation
        if conversation.application_id:
            from types import SimpleNamespace

            from .application_runtime import resolve_draft_runtime, resolve_published_runtime

            runtime = (
                resolve_published_runtime(conversation.application, conversation.application_version)
                if conversation.application_version_id
                else resolve_draft_runtime(conversation.application)
            )
            if len(runtime.knowledge_bases) != 1:
                raise AgentExecutionError(
                    "应用Agent仅支持一个知识库，请修改配置后重新发布。",
                    "AGENT_KNOWLEDGE_SCOPE_INVALID",
                )
            knowledge_base = runtime.knowledge_bases[0].knowledge_base
            agent_target = SimpleNamespace(
                chat_model_config=runtime.chat_target.chat_model_config,
                agent_system_prompt=runtime.agent_system_prompt,
                enabled_tools=runtime.enabled_tools,
                agent_max_steps=runtime.agent_max_steps,
            )
            public_access = conversation.owner_id is None
        else:
            knowledge_base = conversation.knowledge_base
            agent_target = knowledge_base
            public_access = False
        config = resolve_chat_config(agent_target)
        if not config:
            raise AgentExecutionError(
                "Agent模式需要可用的Chat模型，请先配置模型或关闭Agent模式。",
                "AGENT_MODEL_REQUIRED",
            )
        enabled_tools = tuple(agent_target.enabled_tools)
        if public_access:
            enabled_tools = tuple(
                item
                for item in enabled_tools
                if (definition := get_tool(item))
                and definition.allow_public
                and not definition.requires_owner
                and definition.side_effect_level == "NONE"
            )
        tool_schemas = get_tool_schemas(list(enabled_tools))
        messages = _initial_messages(agent_run, agent_target)
        context = ToolContext(
            user=conversation.owner,
            knowledge_base=knowledge_base,
            conversation=conversation,
            agent_run=agent_run,
            enabled_tools=enabled_tools,
            public_access=public_access,
        )
        total_tool_calls = 0
        total_tool_result_chars = 0
        gathered_references: list[dict] = []

        yield _agent_event(
            "agent_start",
            {"agent_run_id": agent_run.id, "max_steps": agent_target.agent_max_steps},
        )
        with create_openai_client(config) as client:
            for step in range(1, agent_target.agent_max_steps + 1):
                step_count = step
                agent_run.step_count = step_count
                agent_run.save(update_fields=["step_count"])
                if monotonic() - started > MAX_AGENT_RUNTIME_SECONDS:
                    raise AgentExecutionError("Agent执行超时，请缩小任务后重试", "AGENT_TIMEOUT")
                yield _agent_event("agent_step", {"step": step, "max_steps": agent_target.agent_max_steps})
                request_options = {
                    "model": config.model_name,
                    "messages": messages,
                    "stream": True,
                }
                if tool_schemas:
                    request_options.update({"tools": tool_schemas, "tool_choice": "auto"})
                try:
                    stream = client.chat.completions.create(**request_options)
                    content_parts, tool_calls = _collect_model_turn(stream)
                except AgentExecutionError:
                    raise
                except Exception as exc:
                    raise _safe_agent_model_error(exc) from exc

                if not tool_calls:
                    answer = "".join(content_parts)
                    if not answer.strip():
                        raise AgentExecutionError("模型没有返回有效回答", "AGENT_INVALID_RESPONSE")
                    for content in content_parts:
                        if content:
                            yield _agent_event("content", {"content": content})
                    visible_references = public_references(
                        _deduplicate_references(gathered_references)
                    )
                    assistant = save_assistant_message(
                        agent_run.conversation,
                        answer,
                        visible_references,
                    )
                    finish_agent_run(
                        agent_run,
                        AgentRun.Status.SUCCESS,
                        step_count=step_count,
                        assistant_message=assistant,
                    )
                    terminal = True
                    yield _agent_event("references", visible_references)
                    yield _agent_event(
                        "agent_done",
                        {"agent_run_id": agent_run.id, "status": AgentRun.Status.SUCCESS, "step_count": step_count},
                    )
                    yield _agent_event("done", {})
                    return

                if len(tool_calls) > MAX_TOOL_CALLS_PER_STEP:
                    raise AgentExecutionError(
                        "模型单步请求的工具数量超过限制",
                        "TOOL_CALL_LIMIT",
                    )
                if total_tool_calls + len(tool_calls) > MAX_TOOL_CALLS_PER_RUN:
                    raise AgentExecutionError("Agent工具调用总数超过限制", "TOOL_CALL_LIMIT")
                if step == agent_target.agent_max_steps:
                    raise AgentExecutionError(
                        "达到最大执行步骤，Agent已停止",
                        "AGENT_STEP_LIMIT",
                        status=AgentRun.Status.LIMIT_REACHED,
                    )

                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(content_parts) or None,
                        "tool_calls": [call.openai_message_value() for call in tool_calls],
                    }
                )
                for sequence, call in enumerate(tool_calls, start=1):
                    total_tool_calls += 1
                    try:
                        definition, arguments, execution = _prepare_tool(
                            context,
                            call,
                            step=step,
                            sequence=sequence,
                        )
                    except ToolError as exc:
                        execution = agent_run.tool_executions.filter(
                            step=step,
                            sequence=sequence,
                        ).first()
                        yield _agent_event(
                            "tool_error",
                            {
                                "execution_id": execution.id if execution else None,
                                "step": step,
                                "sequence": sequence,
                                "tool_name": call.name or "unknown",
                                "status": execution.status if execution else ToolExecution.Status.FAILURE,
                                "error_code": exc.error_code,
                                "error_message": exc.message,
                                "latency_ms": execution.latency_ms if execution else 0,
                            },
                        )
                        raise AgentExecutionError(exc.message, exc.error_code) from exc

                    yield _agent_event(
                        "tool_start",
                        {
                            "execution_id": execution.id,
                            "step": step,
                            "sequence": sequence,
                            "tool_name": execution.tool_name,
                            "arguments": arguments,
                        },
                    )
                    try:
                        result, result_chars = _execute_tool(
                            context,
                            definition,
                            arguments,
                            execution,
                        )
                    except ToolError as exc:
                        execution.refresh_from_db()
                        yield _agent_event(
                            "tool_error",
                            {
                                "execution_id": execution.id,
                                "step": step,
                                "sequence": sequence,
                                "tool_name": execution.tool_name,
                                "status": execution.status,
                                "error_code": exc.error_code,
                                "error_message": exc.message,
                                "latency_ms": execution.latency_ms,
                            },
                        )
                        raise AgentExecutionError(exc.message, exc.error_code) from exc
                    total_tool_result_chars += result_chars
                    if total_tool_result_chars > MAX_TOTAL_TOOL_RESULT_CHARS:
                        raise AgentExecutionError(
                            "Agent累计工具结果超过安全长度限制",
                            "TOOL_RESULT_TOO_LARGE",
                        )
                    for reference in result.references:
                        if conversation.application_id:
                            reference = {
                                **reference,
                                "knowledge_base_id": knowledge_base.id,
                                "knowledge_base_name": knowledge_base.name,
                            }
                        gathered_references.append(reference)
                    yield _agent_event(
                        "tool_result",
                        {
                            "execution_id": execution.id,
                            "step": step,
                            "sequence": sequence,
                            "tool_name": execution.tool_name,
                            "status": execution.status,
                            "result_summary": execution.result_summary,
                            "result_payload": execution.result_payload,
                            "latency_ms": execution.latency_ms,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.tool_call_id,
                            "content": json.dumps(
                                result.model_payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
    except GeneratorExit:
        if not terminal:
            finish_agent_run(
                agent_run,
                AgentRun.Status.CANCELLED,
                step_count=step_count,
                error_code="AGENT_CANCELLED",
                error_message="Agent执行已取消",
            )
        raise
    except AgentExecutionError as exc:
        finish_agent_run(
            agent_run,
            exc.status,
            step_count=step_count,
            error_code=exc.error_code,
            error_message=exc.message,
        )
        terminal = True
        yield _agent_event(
            "agent_done",
            {
                "agent_run_id": agent_run.id,
                "status": exc.status,
                "step_count": step_count,
                "error_code": exc.error_code,
                "error_message": exc.message,
            },
        )
        yield _agent_event("error", {"message": exc.message, "error_code": exc.error_code})
    except Exception:
        message = "Agent执行失败，请稍后重试"
        finish_agent_run(
            agent_run,
            AgentRun.Status.FAILURE,
            step_count=step_count,
            error_code="AGENT_INVALID_RESPONSE",
            error_message=message,
        )
        terminal = True
        yield _agent_event(
            "agent_done",
            {
                "agent_run_id": agent_run.id,
                "status": AgentRun.Status.FAILURE,
                "step_count": step_count,
                "error_code": "AGENT_INVALID_RESPONSE",
                "error_message": message,
            },
        )
        yield _agent_event("error", {"message": message, "error_code": "AGENT_INVALID_RESPONSE"})
