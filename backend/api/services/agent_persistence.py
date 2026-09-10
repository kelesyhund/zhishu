from django.utils import timezone

from api.models import AgentRun, Conversation, Message, ToolExecution


def create_agent_run(conversation: Conversation, user_message: Message) -> AgentRun:
    if user_message.conversation_id != conversation.id or user_message.role != Message.Role.USER:
        raise ValueError("Agent用户消息与会话不匹配")
    return AgentRun.objects.create(conversation=conversation, user_message=user_message)


def finish_agent_run(
    agent_run: AgentRun,
    status: str,
    *,
    step_count: int,
    error_code: str = "",
    error_message: str = "",
    assistant_message: Message | None = None,
) -> None:
    agent_run.status = status
    agent_run.step_count = step_count
    agent_run.error_code = error_code[:50]
    agent_run.error_message = error_message[:500]
    agent_run.assistant_message = assistant_message
    agent_run.finished_at = timezone.now()
    agent_run.save(
        update_fields=[
            "status",
            "step_count",
            "error_code",
            "error_message",
            "assistant_message",
            "finished_at",
        ]
    )


def create_tool_execution(
    agent_run: AgentRun,
    *,
    step: int,
    sequence: int,
    tool_call_id: str,
    tool_name: str,
    arguments: dict,
) -> ToolExecution:
    return ToolExecution.objects.create(
        agent_run=agent_run,
        step=step,
        sequence=sequence,
        tool_call_id=tool_call_id[:200],
        tool_name=tool_name[:100],
        arguments=arguments,
    )


def finish_tool_execution(
    execution: ToolExecution,
    status: str,
    *,
    latency_ms: int,
    result_summary: str = "",
    result_payload: dict | None = None,
    error_code: str = "",
    error_message: str = "",
) -> None:
    execution.status = status
    execution.latency_ms = max(0, latency_ms)
    execution.result_summary = result_summary[:500]
    execution.result_payload = result_payload or {}
    execution.error_code = error_code[:50]
    execution.error_message = error_message[:500]
    execution.finished_at = timezone.now()
    execution.save(
        update_fields=[
            "status",
            "latency_ms",
            "result_summary",
            "result_payload",
            "error_code",
            "error_message",
            "finished_at",
        ]
    )
