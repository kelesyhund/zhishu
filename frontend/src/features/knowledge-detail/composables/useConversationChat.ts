import { computed, nextTick, ref, type ComputedRef, type Ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteConversation,
  getConversation,
  listConversationMessages,
  listConversations,
  streamChat,
  updateConversationTitle,
  type AgentConfig,
  type AgentRunStatus,
  type AgentRunTrace,
  type ConversationItem,
  type MessageItem,
  type ToolExecutionItem,
} from '../../../api'
import { errorMessage, errorStatus } from '../../../api/client'
import { isAbortError } from '../../../api/sse'
import { conversationIdFromQuery, withConversationQuery } from '../conversationRoute'
import { isRecord, isReferenceItems, type ChatMessage } from '../types'
import { useRequestGuard } from './useRequestGuard'

export type GenerationState = 'IDLE' | 'CONNECTING' | 'STREAMING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

interface ConversationChatOptions {
  route: RouteLocationNormalizedLoaded
  router: Router
  hasDocuments: ComputedRef<boolean>
  agentEnabled: ComputedRef<boolean>
  agentConfig: Ref<AgentConfig | null>
  ensureTrace: (answer: ChatMessage, id: number) => AgentRunTrace
  updateTool: (trace: AgentRunTrace, execution: ToolExecutionItem) => void
}

function toChatMessage(message: MessageItem): ChatMessage {
  return { id: message.id, role: message.role, content: message.content, references: message.references, agentTrace: message.agent_trace, created_at: message.created_at }
}

export function useConversationChat(knowledgeId: number, options: ConversationChatOptions) {
  const drawerVisible = ref(false)
  const conversations = ref<ConversationItem[]>([])
  const conversationLoading = ref(false)
  const conversationPage = ref(1)
  const conversationPageSize = ref(20)
  const conversationTotal = ref(0)
  const activeConversationId = ref<number | null>(null)
  const activeConversation = ref<ConversationItem | null>(null)
  const historyLoading = ref(false)
  const historyHasPrevious = ref(false)
  const historyPreviousPage = ref<number | null>(null)
  const renamingId = ref<number | null>(null)
  const deletingId = ref<number | null>(null)
  const input = ref('')
  const sending = ref(false)
  const generationState = ref<GenerationState>('IDLE')
  const messages = ref<ChatMessage[]>([])
  const chatBox = ref<HTMLElement>()
  const historyGuard = useRequestGuard()
  const conversationGuard = useRequestGuard()
  let streamSequence = 0
  let streamController: AbortController | null = null
  let flushTimer: ReturnType<typeof setTimeout> | null = null
  let bufferedContent = ''

  const activeTitle = computed(() => activeConversation.value?.title || conversations.value.find((item) => item.id === activeConversationId.value)?.title || '新对话')

  async function setQuery(id: number | null) {
    await options.router.replace({ query: withConversationQuery(options.route.query, id) })
  }

  async function loadConversations(page = conversationPage.value) {
    const sequence = conversationGuard.next()
    conversationLoading.value = true
    try {
      const value = await listConversations(knowledgeId, page, conversationPageSize.value)
      if (!conversationGuard.current(sequence)) return
      conversations.value = value.items
      conversationTotal.value = value.total
      conversationPage.value = value.page
      conversationPageSize.value = value.page_size
    } catch (error) {
      if (conversationGuard.current(sequence)) ElMessage.error(errorMessage(error))
    } finally { if (conversationGuard.current(sequence)) conversationLoading.value = false }
  }

  async function openHistory() { drawerVisible.value = true; await loadConversations(1) }
  async function scrollBottom() { await nextTick(); if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight }

  function clearFlushTimer() {
    if (flushTimer !== null) clearTimeout(flushTimer)
    flushTimer = null
  }
  function flushContent(answer: ChatMessage) {
    clearFlushTimer()
    if (bufferedContent) { answer.content += bufferedContent; bufferedContent = '' }
  }
  function queueContent(answer: ChatMessage, content: string) {
    bufferedContent += content
    if (flushTimer === null) flushTimer = setTimeout(() => { flushContent(answer); void scrollBottom() }, 32)
  }

  function cancelStream(state: GenerationState = 'CANCELLED') {
    streamSequence += 1
    streamController?.abort()
    streamController = null
    clearFlushTimer()
    bufferedContent = ''
    sending.value = false
    if (generationState.value === 'CONNECTING' || generationState.value === 'STREAMING') generationState.value = state
  }
  function stopGenerating() {
    cancelStream('CANCELLED')
    void loadConversations(1)
    ElMessage.info('已停止生成')
  }

  async function selectConversation(id: number, updateUrl = true) {
    if (sending.value) cancelStream()
    const sequence = historyGuard.next()
    historyLoading.value = true
    try {
      const [conversation, page] = await Promise.all([
        getConversation(knowledgeId, id), listConversationMessages(knowledgeId, id, 'last', 50),
      ])
      if (!historyGuard.current(sequence)) return
      activeConversationId.value = conversation.id
      activeConversation.value = conversation
      messages.value = page.items.map(toChatMessage)
      historyHasPrevious.value = page.has_previous
      historyPreviousPage.value = page.previous_page
      generationState.value = 'IDLE'
      if (updateUrl) await setQuery(conversation.id)
      drawerVisible.value = false
      await scrollBottom()
    } catch (error) {
      if (!historyGuard.current(sequence)) return
      if (errorStatus(error) === 404) {
        activeConversationId.value = null; activeConversation.value = null; messages.value = []
        historyHasPrevious.value = false; historyPreviousPage.value = null
        await setQuery(null); ElMessage.warning('会话不存在或无权访问，已进入新对话')
      } else ElMessage.error(errorMessage(error))
    } finally { if (historyGuard.current(sequence)) historyLoading.value = false }
  }

  async function loadOlderMessages() {
    const id = activeConversationId.value
    const pageNumber = historyPreviousPage.value
    if (!id || !pageNumber || historyLoading.value) return
    const sequence = historyGuard.next()
    const previousHeight = chatBox.value?.scrollHeight || 0
    historyLoading.value = true
    try {
      const page = await listConversationMessages(knowledgeId, id, pageNumber, 50)
      if (!historyGuard.current(sequence) || id !== activeConversationId.value) return
      const existing = new Set(messages.value.flatMap((message) => message.id ? [message.id] : []))
      messages.value = [...page.items.filter((message) => !existing.has(message.id)).map(toChatMessage), ...messages.value]
      historyHasPrevious.value = page.has_previous
      historyPreviousPage.value = page.previous_page
      await nextTick()
      if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight - previousHeight
    } catch (error) { if (historyGuard.current(sequence)) ElMessage.error(errorMessage(error)) }
    finally { if (historyGuard.current(sequence)) historyLoading.value = false }
  }

  async function startNewConversation() {
    cancelStream(); historyGuard.invalidate()
    activeConversationId.value = null; activeConversation.value = null; messages.value = []
    historyLoading.value = false; historyHasPrevious.value = false; historyPreviousPage.value = null
    drawerVisible.value = false; generationState.value = 'IDLE'
    await setQuery(null)
  }

  async function renameConversation(conversation: ConversationItem) {
    if (sending.value || renamingId.value !== null) return
    let title = ''
    try {
      const result = await ElMessageBox.prompt('请输入新的会话标题', '修改会话标题', {
        inputValue: conversation.title, inputPlaceholder: '最多100个字符', confirmButtonText: '保存', cancelButtonText: '取消',
        inputValidator: (value) => value.trim() ? (value.trim().length <= 100 || '会话标题不能超过100个字符') : '会话标题不能为空',
      })
      title = result.value.trim()
    } catch { return }
    renamingId.value = conversation.id
    try {
      const updated = await updateConversationTitle(knowledgeId, conversation.id, title)
      const index = conversations.value.findIndex((item) => item.id === conversation.id)
      if (index >= 0) conversations.value.splice(index, 1, updated)
      if (activeConversationId.value === conversation.id) activeConversation.value = updated
      ElMessage.success('会话标题已修改')
    } catch (error) { ElMessage.error(errorMessage(error)) }
    finally { renamingId.value = null }
  }

  async function removeConversation(conversation: ConversationItem) {
    if (sending.value || deletingId.value !== null) return
    try { await ElMessageBox.confirm(`确认删除会话“${conversation.title}”吗？该会话中的全部消息都会被删除。`, '删除会话', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }) }
    catch { return }
    deletingId.value = conversation.id
    try {
      await deleteConversation(knowledgeId, conversation.id)
      if (activeConversationId.value === conversation.id) await startNewConversation()
      if (conversations.value.length === 1 && conversationPage.value > 1) conversationPage.value -= 1
      await loadConversations(conversationPage.value)
      ElMessage.success('会话及其消息已删除')
    } catch (error) { ElMessage.error(errorMessage(error)) }
    finally { deletingId.value = null }
  }

  async function restoreFromUrl() {
    const id = conversationIdFromQuery(options.route.query)
    if (options.route.query.conversation && id === null) { await setQuery(null); return }
    if (id !== null) await selectConversation(id, false)
  }

  async function send() {
    const question = input.value.trim()
    if (!question || sending.value) return
    if (options.agentEnabled.value && !options.agentConfig.value?.chat_model_status.available) return ElMessage.warning('Agent模式需要可用的Chat模型，请先配置模型')
    if (!options.agentEnabled.value && !options.hasDocuments.value) return ElMessage.warning('请先上传并成功处理一个文档')

    const sequence = ++streamSequence
    const controller = new AbortController()
    streamController = controller
    messages.value.push({ role: 'user', content: question, references: [] })
    const answer: ChatMessage = { role: 'assistant', content: '', references: [] }
    messages.value.push(answer)
    input.value = ''; sending.value = true; generationState.value = 'CONNECTING'
    await scrollBottom()
    try {
      await streamChat(knowledgeId, question, activeConversationId.value, (event, data) => {
        if (sequence !== streamSequence) return
        if (generationState.value === 'CONNECTING') generationState.value = 'STREAMING'
        if (event === 'meta' && isRecord(data) && typeof data.conversation_id === 'number') {
          activeConversationId.value = data.conversation_id; activeConversation.value = null
          void setQuery(data.conversation_id).catch(() => undefined)
        } else if (event === 'content' && isRecord(data) && typeof data.content === 'string') queueContent(answer, data.content)
        else if (event === 'agent_start' && isRecord(data) && typeof data.agent_run_id === 'number') options.ensureTrace(answer, data.agent_run_id)
        else if (event === 'agent_step' && isRecord(data) && typeof data.step === 'number' && answer.agentTrace) answer.agentTrace.step_count = data.step
        else if (event === 'tool_start' && isRecord(data) && typeof data.execution_id === 'number' && typeof data.step === 'number' && typeof data.sequence === 'number' && typeof data.tool_name === 'string' && isRecord(data.arguments) && answer.agentTrace) {
          options.updateTool(answer.agentTrace, { id: data.execution_id, step: data.step, sequence: data.sequence, tool_call_id: '', tool_name: data.tool_name, arguments: data.arguments, result_summary: '', result_payload: {}, status: 'RUNNING', latency_ms: 0, error_code: '', error_message: '' })
        } else if ((event === 'tool_result' || event === 'tool_error') && isRecord(data) && typeof data.execution_id === 'number' && typeof data.step === 'number' && typeof data.sequence === 'number' && typeof data.tool_name === 'string' && typeof data.latency_ms === 'number' && answer.agentTrace) {
          const existing = answer.agentTrace.tool_executions.find((item) => item.id === data.execution_id)
          const success = event === 'tool_result'
          options.updateTool(answer.agentTrace, { id: data.execution_id, step: data.step, sequence: data.sequence, tool_call_id: existing?.tool_call_id || '', tool_name: data.tool_name, arguments: existing?.arguments || {}, result_summary: success && typeof data.result_summary === 'string' ? data.result_summary : '', result_payload: success && isRecord(data.result_payload) ? data.result_payload : {}, status: success ? 'SUCCESS' : data.status === 'REJECTED' ? 'REJECTED' : 'FAILURE', latency_ms: data.latency_ms, error_code: !success && typeof data.error_code === 'string' ? data.error_code : '', error_message: !success && typeof data.error_message === 'string' ? data.error_message : '' })
        } else if (event === 'agent_done' && isRecord(data) && typeof data.agent_run_id === 'number' && typeof data.status === 'string') {
          const trace = options.ensureTrace(answer, data.agent_run_id)
          if (['SUCCESS', 'FAILURE', 'LIMIT_REACHED', 'CANCELLED'].includes(data.status)) trace.status = data.status as AgentRunStatus
          if (typeof data.step_count === 'number') trace.step_count = data.step_count
          if (typeof data.error_code === 'string') trace.error_code = data.error_code
          if (typeof data.error_message === 'string') trace.error_message = data.error_message
        } else if (event === 'references' && isReferenceItems(data)) answer.references = data
        else if (event === 'error' && isRecord(data) && typeof data.message === 'string') throw new Error(data.message)
      }, controller.signal)
      if (sequence === streamSequence) { flushContent(answer); generationState.value = 'COMPLETED' }
    } catch (error) {
      if (sequence !== streamSequence) return
      flushContent(answer)
      if (isAbortError(error)) generationState.value = 'CANCELLED'
      else { generationState.value = 'FAILED'; answer.content += `\n\n请求失败：${errorMessage(error)}` }
    } finally {
      if (sequence === streamSequence) {
        streamController = null; sending.value = false
        await loadConversations(1); await scrollBottom()
      }
    }
  }

  function dispose() {
    cancelStream(); historyGuard.invalidate(); conversationGuard.invalidate()
  }

  return {
    drawerVisible, conversations, conversationLoading, conversationPage, conversationPageSize,
    conversationTotal, activeConversationId, activeConversation, activeTitle, historyLoading,
    historyHasPrevious, historyPreviousPage, renamingId, deletingId, input, sending, generationState,
    messages, chatBox, loadConversations, openHistory, selectConversation, loadOlderMessages,
    startNewConversation, renameConversation, removeConversation, restoreFromUrl, send, stopGenerating, dispose,
  }
}
