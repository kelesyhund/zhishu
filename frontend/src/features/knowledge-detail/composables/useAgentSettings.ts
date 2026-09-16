import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getAgentConfig,
  updateAgentConfig,
  type AgentConfig,
  type AgentRunTrace,
  type ToolExecutionItem,
} from '../../../api'
import { errorMessage } from '../../../api/client'
import type { ChatMessage } from '../types'
import { useRequestGuard } from './useRequestGuard'

export function useAgentSettings(knowledgeId: number) {
  const visible = ref(false)
  const config = ref<AgentConfig | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const guard = useRequestGuard()
  const form = reactive({ agent_enabled: false, agent_max_steps: 5, agent_system_prompt: '', enabled_tools: [] as string[] })
  const enabled = computed(() => Boolean(config.value?.agent_enabled))

  function copy(value: AgentConfig) {
    form.agent_enabled = value.agent_enabled
    form.agent_max_steps = value.agent_max_steps
    form.agent_system_prompt = value.agent_system_prompt
    form.enabled_tools = [...value.enabled_tools]
  }

  function resetDefaults() {
    form.agent_enabled = false
    form.agent_max_steps = 5
    form.agent_system_prompt = ''
    form.enabled_tools = []
  }

  async function load(showError = true) {
    const sequence = guard.next()
    loading.value = true
    try {
      const result = await getAgentConfig(knowledgeId)
      if (!guard.current(sequence)) return
      config.value = result
      copy(result)
    } catch (error) {
      if (guard.current(sequence) && showError) ElMessage.error(errorMessage(error))
    } finally {
      if (guard.current(sequence)) loading.value = false
    }
  }

  async function open() {
    visible.value = true
    await load()
  }

  async function save() {
    if (saving.value) return
    saving.value = true
    try {
      const result = await updateAgentConfig(knowledgeId, {
        agent_enabled: form.agent_enabled,
        agent_max_steps: form.agent_max_steps,
        agent_system_prompt: form.agent_system_prompt,
        enabled_tools: [...form.enabled_tools],
      })
      config.value = result
      copy(result)
      visible.value = false
      ElMessage.success('Agent配置已更新')
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      saving.value = false
    }
  }

  function ensureTrace(answer: ChatMessage, agentRunId: number): AgentRunTrace {
    if (!answer.agentTrace || answer.agentTrace.id !== agentRunId) {
      answer.agentTrace = { id: agentRunId, status: 'RUNNING', step_count: 0, error_code: '', error_message: '', tool_executions: [] }
    }
    return answer.agentTrace
  }

  function updateTool(trace: AgentRunTrace, execution: ToolExecutionItem) {
    const index = trace.tool_executions.findIndex((item) => item.id === execution.id)
    if (index >= 0) trace.tool_executions.splice(index, 1, execution)
    else trace.tool_executions.push(execution)
  }

  function dispose() {
    guard.invalidate()
  }

  return { visible, config, loading, saving, form, enabled, resetDefaults, load, open, save, ensureTrace, updateTool, dispose }
}
