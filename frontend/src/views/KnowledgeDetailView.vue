<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  ChatLineRound,
  Delete,
  Document,
  EditPen,
  Plus,
  Refresh,
  Setting,
  UploadFilled,
  View as ViewIcon,
} from '@element-plus/icons-vue'
import { marked } from 'marked'

import {
  deleteConversation,
  deleteDocument,
  cancelDocumentProcessingTask,
  compareRetrieval,
  debugRetrieval,
  getAgentConfig,
  getConversation,
  getDocumentChunkingConfig,
  getKnowledgeBase,
  getKnowledgeBaseModelStatus,
  getRetrievalConfig,
  getRetrievalCapabilities,
  listModelConfigs,
  listConversationMessages,
  listConversations,
  listDocumentParagraphs,
  listDocumentProcessingTasks,
  listDocuments,
  previewDocumentChunks,
  reindexDocument,
  reprocessDocument,
  retryDocumentProcessingTask,
  streamChat,
  updateAgentConfig,
  updateRetrievalConfig,
  updateKnowledgeBaseModelStatus,
  updateConversationTitle,
  updateDocumentChunkingConfig,
  uploadDocument,
  type AgentConfig,
  type AgentRunStatus,
  type AgentRunTrace,
  type ConversationItem,
  type DocumentItem,
  type DocumentChunkingConfig,
  type DocumentChunkPreview,
  type DocumentProcessingTaskItem,
  type DocumentTaskStage,
  type DocumentTaskStatus,
  type KnowledgeBase,
  type KnowledgeBaseModelStatus,
  type MessageItem,
  type ModelConfigItem,
  type ParagraphItem,
  type ReferenceItem,
  type RetrievalConfig,
  type RetrievalCapabilities,
  type RetrievalCompareResult,
  type RetrievalDebugResult,
  type ToolExecutionItem,
  type ToolExecutionStatus,
} from '../api'
import { errorMessage, errorStatus } from '../api/client'
import BrandLogo from '../components/BrandLogo.vue'

interface ChatMessage {
  id?: number
  role: 'user' | 'assistant'
  content: string
  references: ReferenceItem[]
  agentTrace?: AgentRunTrace | null
  created_at?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isReferenceItems(value: unknown): value is ReferenceItem[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        isRecord(item) &&
        typeof item.document_name === 'string' &&
        typeof item.content === 'string' &&
        typeof item.similarity === 'number',
    )
  )
}

const route = useRoute()
const router = useRouter()
const knowledgeId = Number(route.params.id)
const knowledgeBase = ref<KnowledgeBase | null>(null)
const documents = ref<DocumentItem[]>([])
const uploading = ref(false)
const deletingId = ref<number | null>(null)
const reprocessingId = ref<number | null>(null)
const processingTasks = ref<DocumentProcessingTaskItem[]>([])
const taskLoading = ref(false)
const retryingTaskId = ref<number | null>(null)
const cancellingTaskId = ref<number | null>(null)
const polling = ref(false)
let pollRequestSequence = 0
let pollTimer: ReturnType<typeof setTimeout> | null = null
const paragraphDrawerVisible = ref(false)
const previewDocument = ref<DocumentItem | null>(null)
const paragraphs = ref<ParagraphItem[]>([])
const paragraphLoading = ref(false)
const paragraphPage = ref(1)
const paragraphPageSize = ref(20)
const paragraphTotal = ref(0)
const chunkSettingsVisible = ref(false)
const chunkConfigDocument = ref<DocumentItem | null>(null)
const chunkConfigLoading = ref(false)
const chunkPreviewLoading = ref(false)
const chunkReindexing = ref(false)
const chunkPreview = ref<DocumentChunkPreview | null>(null)
const chunkConfigForm = reactive<DocumentChunkingConfig>({
  parser_type: 'AUTO',
  chunk_strategy: 'PARENT_CHILD',
  parent_max_tokens: 1500,
  child_target_tokens: 400,
  child_overlap_tokens: 60,
  preserve_tables: true,
  preserve_code_blocks: true,
})
let chunkRequestSequence = 0
const conversationDrawerVisible = ref(false)
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
const renamingConversationId = ref<number | null>(null)
const deletingConversationId = ref<number | null>(null)
const input = ref('')
const sending = ref(false)
const messages = ref<ChatMessage[]>([])
const modelSettingsVisible = ref(false)
const modelStatus = ref<KnowledgeBaseModelStatus | null>(null)
const modelStatusLoading = ref(false)
const modelSettingsSaving = ref(false)
const chatConfigs = ref<ModelConfigItem[]>([])
const embeddingConfigs = ref<ModelConfigItem[]>([])
const selectedChatConfigId = ref<number | null>(null)
const selectedEmbeddingConfigId = ref<number | null>(null)
const retrievalSettingsVisible = ref(false)
const retrievalConfig = ref<RetrievalConfig | null>(null)
const retrievalConfigLoading = ref(false)
const retrievalConfigSaving = ref(false)
const retrievalForm = reactive<RetrievalConfig>({
  retrieval_mode: 'VECTOR',
  retrieval_top_k: 5,
  similarity_threshold: 0,
  vector_weight: 1,
  fusion_method: 'WEIGHTED',
  vector_candidate_k: 30,
  keyword_candidate_k: 30,
  rrf_k: 60,
  rerank_enabled: false,
  rerank_candidate_k: 20,
  max_context_chars: 6000,
  system_prompt: '',
  no_answer_message: '',
})
const retrievalDebugVisible = ref(false)
const retrievalQuery = ref('')
const retrievalDebugLoading = ref(false)
const retrievalDebugResult = ref<RetrievalDebugResult | null>(null)
const retrievalCapabilities = ref<RetrievalCapabilities | null>(null)
const retrievalCapabilitiesLoading = ref(false)
const retrievalCompareLoading = ref(false)
const retrievalCompareResult = ref<RetrievalCompareResult | null>(null)
let retrievalDebugRequestSequence = 0
let retrievalCompareRequestSequence = 0
const agentSettingsVisible = ref(false)
const agentConfig = ref<AgentConfig | null>(null)
const agentConfigLoading = ref(false)
const agentConfigSaving = ref(false)
const agentForm = reactive({
  agent_enabled: false,
  agent_max_steps: 5,
  agent_system_prompt: '',
  enabled_tools: [] as string[],
})
let agentRequestSequence = 0
const chatBox = ref<HTMLElement>()
let historyRequestSequence = 0
const hasDocuments = computed(() =>
  documents.value.some((item) => item.status === 'SUCCESS' && !item.needs_reprocess),
)
const modelSummary = computed(() => {
  if (!modelStatus.value) return '正在读取模型状态'
  return `Chat：${modelStatus.value.chat.label} · Embedding：${modelStatus.value.embedding.label}`
})
const activeConversationTitle = computed(
  () =>
    activeConversation.value?.title ||
    conversations.value.find((item) => item.id === activeConversationId.value)?.title ||
    '新对话',
)
const keywordWeight = computed(() => Number((1 - retrievalForm.vector_weight).toFixed(2)))
const agentEnabled = computed(() => Boolean(agentConfig.value?.agent_enabled))
const activeTaskStatuses: DocumentTaskStatus[] = [
  'PENDING',
  'PROCESSING',
  'RETRYING',
  'CANCEL_REQUESTED',
]
const activeTasks = computed(() =>
  processingTasks.value.filter((task) => activeTaskStatuses.includes(task.status)),
)

function copyAgentConfig(config: AgentConfig) {
  agentForm.agent_enabled = config.agent_enabled
  agentForm.agent_max_steps = config.agent_max_steps
  agentForm.agent_system_prompt = config.agent_system_prompt
  agentForm.enabled_tools = [...config.enabled_tools]
}

function resetAgentDefaults() {
  agentForm.agent_enabled = false
  agentForm.agent_max_steps = 5
  agentForm.agent_system_prompt = ''
  agentForm.enabled_tools = []
}

async function loadAgentConfig(showError = true) {
  agentConfigLoading.value = true
  try {
    const config = await getAgentConfig(knowledgeId)
    agentConfig.value = config
    copyAgentConfig(config)
  } catch (error) {
    if (showError) ElMessage.error(errorMessage(error))
  } finally {
    agentConfigLoading.value = false
  }
}

async function openAgentSettings() {
  agentSettingsVisible.value = true
  await loadAgentConfig()
}

async function saveAgentSettings() {
  if (agentConfigSaving.value) return
  agentConfigSaving.value = true
  try {
    const config = await updateAgentConfig(knowledgeId, {
      agent_enabled: agentForm.agent_enabled,
      agent_max_steps: agentForm.agent_max_steps,
      agent_system_prompt: agentForm.agent_system_prompt,
      enabled_tools: [...agentForm.enabled_tools],
    })
    agentConfig.value = config
    copyAgentConfig(config)
    agentSettingsVisible.value = false
    ElMessage.success('Agent配置已更新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    agentConfigSaving.value = false
  }
}

function copyRetrievalConfig(config: RetrievalConfig) {
  Object.assign(retrievalForm, config)
}

function resetRetrievalDefaults() {
  copyRetrievalConfig({
    retrieval_mode: 'VECTOR',
    retrieval_top_k: 5,
    similarity_threshold: 0,
    vector_weight: 1,
    fusion_method: 'WEIGHTED',
    vector_candidate_k: 30,
    keyword_candidate_k: 30,
    rrf_k: 60,
    rerank_enabled: false,
    rerank_candidate_k: 20,
    max_context_chars: 6000,
    system_prompt: '',
    no_answer_message: '',
  })
}

async function loadRetrievalConfig(showError = true) {
  retrievalConfigLoading.value = true
  try {
    const config = await getRetrievalConfig(knowledgeId)
    retrievalConfig.value = config
    copyRetrievalConfig(config)
  } catch (error) {
    if (showError) ElMessage.error(errorMessage(error))
  } finally {
    retrievalConfigLoading.value = false
  }
}

async function loadRetrievalCapabilities(showError = true) {
  retrievalCapabilitiesLoading.value = true
  try {
    retrievalCapabilities.value = await getRetrievalCapabilities()
  } catch (error) {
    retrievalCapabilities.value = null
    if (showError) ElMessage.error(errorMessage(error))
  } finally {
    retrievalCapabilitiesLoading.value = false
  }
}

async function openRetrievalSettings() {
  retrievalSettingsVisible.value = true
  await Promise.all([loadRetrievalConfig(), loadRetrievalCapabilities(false)])
}

async function saveRetrievalSettings() {
  if (retrievalConfigSaving.value) return
  retrievalConfigSaving.value = true
  try {
    const payload: RetrievalConfig = {
      ...retrievalForm,
      rerank_enabled:
        retrievalForm.retrieval_mode === 'HYBRID' &&
        retrievalForm.fusion_method === 'RRF' &&
        retrievalForm.rerank_enabled,
    }
    const config = await updateRetrievalConfig(knowledgeId, payload)
    retrievalConfig.value = config
    copyRetrievalConfig(config)
    retrievalSettingsVisible.value = false
    retrievalDebugResult.value = null
    retrievalCompareResult.value = null
    ElMessage.success('检索配置已更新，后续问答立即使用新策略')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    retrievalConfigSaving.value = false
  }
}

async function openRetrievalDebug() {
  retrievalDebugVisible.value = true
  retrievalDebugResult.value = null
  retrievalCompareResult.value = null
  await Promise.all([
    retrievalConfig.value ? Promise.resolve() : loadRetrievalConfig(false),
    retrievalCapabilities.value ? Promise.resolve() : loadRetrievalCapabilities(false),
  ])
}

async function runRetrievalCompare() {
  const query = retrievalQuery.value.trim()
  if (!query || retrievalCompareLoading.value) {
    if (!query) ElMessage.warning('请输入测试问题')
    return
  }
  const requestSequence = ++retrievalCompareRequestSequence
  retrievalCompareLoading.value = true
  try {
    const result = await compareRetrieval(
      knowledgeId,
      query,
      {
        retrieval_mode: 'HYBRID',
        fusion_method: 'RRF',
        vector_candidate_k: retrievalForm.vector_candidate_k,
        keyword_candidate_k: retrievalForm.keyword_candidate_k,
        rrf_k: retrievalForm.rrf_k,
        rerank_enabled: Boolean(retrievalCapabilities.value?.reranker_ready),
        rerank_candidate_k: retrievalForm.rerank_candidate_k,
      },
      20,
    )
    if (requestSequence !== retrievalCompareRequestSequence) return
    retrievalCompareResult.value = result
  } catch (error) {
    if (requestSequence === retrievalCompareRequestSequence) {
      retrievalCompareResult.value = null
      ElMessage.error(errorMessage(error))
    }
  } finally {
    if (requestSequence === retrievalCompareRequestSequence) retrievalCompareLoading.value = false
  }
}

async function runRetrievalDebug() {
  const query = retrievalQuery.value.trim()
  if (!query || retrievalDebugLoading.value) {
    if (!query) ElMessage.warning('请输入测试问题')
    return
  }
  const requestSequence = ++retrievalDebugRequestSequence
  retrievalDebugLoading.value = true
  try {
    const result = await debugRetrieval(knowledgeId, query, 20)
    if (requestSequence !== retrievalDebugRequestSequence) return
    retrievalDebugResult.value = result
  } catch (error) {
    if (requestSequence === retrievalDebugRequestSequence) {
      retrievalDebugResult.value = null
      ElMessage.error(errorMessage(error))
    }
  } finally {
    if (requestSequence === retrievalDebugRequestSequence) retrievalDebugLoading.value = false
  }
}

function scoreText(value: number | null) {
  return value === null ? '—' : value.toFixed(4)
}

async function loadKnowledgeBase() {
  try {
    knowledgeBase.value = await getKnowledgeBase(knowledgeId)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function loadDocuments() {
  try {
    documents.value = await listDocuments(knowledgeId)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function taskForDocument(documentId: number) {
  return processingTasks.value.find((task) => task.document_id === documentId) || null
}

function upsertProcessingTask(task: DocumentProcessingTaskItem) {
  processingTasks.value = [task, ...processingTasks.value.filter((item) => item.id !== task.id)]
}

function isTaskActive(task: DocumentProcessingTaskItem | null) {
  return Boolean(task && activeTaskStatuses.includes(task.status))
}

function stopTaskPolling() {
  polling.value = false
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function scheduleTaskPoll(delay = 2000) {
  stopTaskPolling()
  polling.value = true
  pollTimer = setTimeout(() => void loadProcessingTasks(true), delay)
}

async function loadProcessingTasks(fromPoll = false) {
  const requestSequence = ++pollRequestSequence
  if (!fromPoll) taskLoading.value = true
  const previousStatuses = new Map(
    processingTasks.value.map((task) => [task.id, task.status] as const),
  )
  try {
    const result = await listDocumentProcessingTasks(knowledgeId, 1, 100)
    if (requestSequence !== pollRequestSequence) return
    processingTasks.value = result.items
    const becameTerminal = result.items.some((task) => {
      const previous = previousStatuses.get(task.id)
      return previous && activeTaskStatuses.includes(previous) && !activeTaskStatuses.includes(task.status)
    })
    if (becameTerminal) {
      await Promise.all([loadDocuments(), loadModelStatus(false)])
    }
    if (activeTasks.value.length) scheduleTaskPoll()
    else stopTaskPolling()
  } catch (error) {
    if (requestSequence !== pollRequestSequence) return
    stopTaskPolling()
    if (!fromPoll) ElMessage.error(errorMessage(error))
  } finally {
    if (requestSequence === pollRequestSequence) taskLoading.value = false
  }
}

async function loadModelStatus(showError = true) {
  modelStatusLoading.value = true
  try {
    modelStatus.value = await getKnowledgeBaseModelStatus(knowledgeId)
  } catch (error) {
    if (showError) ElMessage.error(errorMessage(error))
  } finally {
    modelStatusLoading.value = false
  }
}

async function openModelSettings() {
  modelSettingsVisible.value = true
  modelStatusLoading.value = true
  try {
    const [status, chatPage, embeddingPage] = await Promise.all([
      getKnowledgeBaseModelStatus(knowledgeId),
      listModelConfigs('CHAT', 1, 100),
      listModelConfigs('EMBEDDING', 1, 100),
    ])
    modelStatus.value = status
    chatConfigs.value = chatPage.items
    embeddingConfigs.value = embeddingPage.items
    selectedChatConfigId.value = status.chat_model_config_id
    selectedEmbeddingConfigId.value = status.embedding_model_config_id
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    modelStatusLoading.value = false
  }
}

async function saveModelSettings() {
  if (modelSettingsSaving.value) return
  modelSettingsSaving.value = true
  try {
    modelStatus.value = await updateKnowledgeBaseModelStatus(knowledgeId, {
      chat_model_config_id: selectedChatConfigId.value,
      embedding_model_config_id: selectedEmbeddingConfigId.value,
    })
    await loadDocuments()
    await loadAgentConfig(false)
    modelSettingsVisible.value = false
    ElMessage.success('知识库模型配置已更新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    modelSettingsSaving.value = false
  }
}

async function chooseFile(event: Event) {
  const inputElement = event.target as HTMLInputElement
  const file = inputElement.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const result = await uploadDocument(knowledgeId, file)
    documents.value.unshift(result.document)
    upsertProcessingTask(result.task)
    await loadModelStatus(false)
    ElMessage.success('文件上传成功，文档已进入后台处理队列')
    scheduleTaskPoll(300)
  } catch (error) {
    ElMessage.error(errorMessage(error))
    await loadDocuments()
  } finally {
    uploading.value = false
    inputElement.value = ''
  }
}

function statusText(status: DocumentItem['status']) {
  return {
    PROCESSING: '处理中',
    SUCCESS: '处理成功',
    FAILURE: '处理失败',
  }[status]
}

function statusTagType(status: DocumentItem['status']): 'info' | 'success' | 'danger' {
  return {
    PROCESSING: 'info',
    SUCCESS: 'success',
    FAILURE: 'danger',
  }[status] as 'info' | 'success' | 'danger'
}

function taskStatusText(status: DocumentTaskStatus) {
  return {
    PENDING: '等待处理',
    PROCESSING: '正在处理',
    RETRYING: '等待自动重试',
    SUCCESS: '处理成功',
    FAILURE: '处理失败',
    ENQUEUE_FAILED: '任务投递失败',
    CANCEL_REQUESTED: '正在取消',
    CANCELLED: '已取消',
  }[status]
}

function taskStageText(stage: DocumentTaskStage) {
  return {
    WAITING: '等待 Worker',
    READING: '读取并解析文件',
    SPLITTING: '切分文本',
    EMBEDDING: '生成向量',
    SAVING: '保存切片',
    DONE: '处理完成',
    FAILED: '处理失败',
    CANCELLING: '等待安全取消',
    CANCELLED: '任务已取消',
  }[stage]
}

function taskTagType(status: DocumentTaskStatus): 'info' | 'success' | 'warning' | 'danger' {
  if (status === 'SUCCESS') return 'success'
  if (status === 'FAILURE' || status === 'ENQUEUE_FAILED') return 'danger'
  if (status === 'RETRYING' || status === 'CANCEL_REQUESTED') return 'warning'
  return 'info'
}

function replaceDocument(document: DocumentItem) {
  const index = documents.value.findIndex((item) => item.id === document.id)
  if (index >= 0) documents.value.splice(index, 1, document)
  if (previewDocument.value?.id === document.id) previewDocument.value = document
}

async function loadParagraphs(page = paragraphPage.value) {
  if (!previewDocument.value) return
  paragraphLoading.value = true
  try {
    const result = await listDocumentParagraphs(
      knowledgeId,
      previewDocument.value.id,
      page,
      paragraphPageSize.value,
    )
    paragraphs.value = result.items
    paragraphTotal.value = result.total
    paragraphPage.value = result.page
    paragraphPageSize.value = result.page_size
  } catch (error) {
    paragraphs.value = []
    paragraphTotal.value = 0
    ElMessage.error(errorMessage(error))
  } finally {
    paragraphLoading.value = false
  }
}

async function openParagraphs(document: DocumentItem) {
  previewDocument.value = document
  paragraphPage.value = 1
  paragraphs.value = []
  paragraphTotal.value = 0
  paragraphDrawerVisible.value = true
  await loadParagraphs(1)
}

function copyChunkConfig(config: DocumentChunkingConfig) {
  Object.assign(chunkConfigForm, config)
}

async function openChunkSettings(document: DocumentItem) {
  const sequence = ++chunkRequestSequence
  chunkConfigDocument.value = document
  chunkPreview.value = null
  chunkSettingsVisible.value = true
  chunkConfigLoading.value = true
  try {
    const config = await getDocumentChunkingConfig(knowledgeId, document.id)
    if (sequence !== chunkRequestSequence || chunkConfigDocument.value?.id !== document.id) return
    copyChunkConfig(config)
  } catch (error) {
    if (sequence === chunkRequestSequence) ElMessage.error(errorMessage(error))
  } finally {
    if (sequence === chunkRequestSequence) chunkConfigLoading.value = false
  }
}

async function runChunkPreview() {
  const document = chunkConfigDocument.value
  if (!document || chunkPreviewLoading.value) return
  const sequence = ++chunkRequestSequence
  chunkPreviewLoading.value = true
  chunkPreview.value = null
  try {
    const result = await previewDocumentChunks(knowledgeId, document.id, { ...chunkConfigForm })
    if (sequence !== chunkRequestSequence || chunkConfigDocument.value?.id !== document.id) return
    chunkPreview.value = result
  } catch (error) {
    if (sequence === chunkRequestSequence) ElMessage.error(errorMessage(error))
  } finally {
    if (sequence === chunkRequestSequence) chunkPreviewLoading.value = false
  }
}

async function saveChunkConfigAndReindex() {
  const document = chunkConfigDocument.value
  if (!document || chunkReindexing.value) return
  try {
    await ElMessageBox.confirm(
      `确认保存“${document.name}”的切片配置并重新索引吗？这会重新解析文件并计算 Embedding；旧切片会保留到新数据完整生成。`,
      '保存并重新索引',
      { type: 'warning', confirmButtonText: '确认重新索引', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  chunkReindexing.value = true
  try {
    const saved = await updateDocumentChunkingConfig(
      knowledgeId,
      document.id,
      { ...chunkConfigForm },
    )
    replaceDocument(saved.document)
    const result = await reindexDocument(knowledgeId, document.id)
    replaceDocument(result.document)
    upsertProcessingTask(result.task)
    chunkSettingsVisible.value = false
    ElMessage.success('切片配置已保存，重新索引任务已进入队列')
    scheduleTaskPoll(300)
  } catch (error) {
    ElMessage.error(errorMessage(error))
    await loadDocuments()
  } finally {
    chunkReindexing.value = false
  }
}

async function reprocess(document: DocumentItem) {
  if (reprocessingId.value !== null) return
  reprocessingId.value = document.id
  try {
    const result = await reprocessDocument(knowledgeId, document.id)
    replaceDocument(result.document)
    upsertProcessingTask(result.task)
    ElMessage.success('重新处理任务已进入队列，旧切片会保留到新处理成功')
    scheduleTaskPoll(300)
  } catch (error) {
    await loadDocuments()
    await loadModelStatus(false)
    ElMessage.error(errorMessage(error))
  } finally {
    reprocessingId.value = null
  }
}

async function retryTask(task: DocumentProcessingTaskItem) {
  if (retryingTaskId.value !== null) return
  retryingTaskId.value = task.id
  try {
    const retried = await retryDocumentProcessingTask(knowledgeId, task.id)
    upsertProcessingTask(retried)
    ElMessage.success('任务已重新进入队列')
    scheduleTaskPoll(300)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    retryingTaskId.value = null
  }
}

async function cancelTask(task: DocumentProcessingTaskItem) {
  if (cancellingTaskId.value !== null) return
  cancellingTaskId.value = task.id
  try {
    const cancelled = await cancelDocumentProcessingTask(knowledgeId, task.id)
    const index = processingTasks.value.findIndex((item) => item.id === cancelled.id)
    if (index >= 0) processingTasks.value.splice(index, 1, cancelled)
    ElMessage.success(cancelled.status === 'CANCELLED' ? '任务已取消' : '取消请求已提交')
    if (isTaskActive(cancelled)) scheduleTaskPoll(300)
    else await Promise.all([loadDocuments(), loadModelStatus(false)])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    cancellingTaskId.value = null
  }
}

async function removeDocument(document: DocumentItem) {
  try {
    await ElMessageBox.confirm(
      `确认删除“${document.name}”吗？删除后，原始文件和全部切片都会被清理。`,
      '删除文档',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingId.value = document.id
  try {
    await deleteDocument(knowledgeId, document.id)
    if (previewDocument.value?.id === document.id) {
      paragraphDrawerVisible.value = false
      previewDocument.value = null
      paragraphs.value = []
      paragraphTotal.value = 0
    }
    await loadDocuments()
    ElMessage.success('文档、切片和原始文件已删除')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    deletingId.value = null
  }
}

function toChatMessage(message: MessageItem): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    references: message.references,
    agentTrace: message.agent_trace,
    created_at: message.created_at,
  }
}

async function setConversationQuery(conversationId: number | null) {
  const query = { ...route.query }
  if (conversationId === null) delete query.conversation
  else query.conversation = String(conversationId)
  await router.replace({ query })
}

async function loadConversations(page = conversationPage.value) {
  conversationLoading.value = true
  try {
    const result = await listConversations(knowledgeId, page, conversationPageSize.value)
    conversations.value = result.items
    conversationTotal.value = result.total
    conversationPage.value = result.page
    conversationPageSize.value = result.page_size
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    conversationLoading.value = false
  }
}

async function openConversationHistory() {
  conversationDrawerVisible.value = true
  await loadConversations(1)
}

async function selectConversation(conversationId: number, updateUrl = true) {
  if (sending.value) return
  const requestSequence = ++historyRequestSequence
  historyLoading.value = true
  try {
    const [conversation, page] = await Promise.all([
      getConversation(knowledgeId, conversationId),
      listConversationMessages(knowledgeId, conversationId, 'last', 50),
    ])
    if (requestSequence !== historyRequestSequence) return

    activeConversationId.value = conversation.id
    activeConversation.value = conversation
    messages.value = page.items.map(toChatMessage)
    historyHasPrevious.value = page.has_previous
    historyPreviousPage.value = page.previous_page
    if (updateUrl) await setConversationQuery(conversation.id)
    conversationDrawerVisible.value = false
    await scrollBottom()
  } catch (error) {
    if (requestSequence !== historyRequestSequence) return
    if (errorStatus(error) === 404) {
      activeConversationId.value = null
      activeConversation.value = null
      messages.value = []
      historyHasPrevious.value = false
      historyPreviousPage.value = null
      await setConversationQuery(null)
      ElMessage.warning('会话不存在或无权访问，已进入新对话')
    } else {
      ElMessage.error(errorMessage(error))
    }
  } finally {
    if (requestSequence === historyRequestSequence) historyLoading.value = false
  }
}

async function loadOlderMessages() {
  const conversationId = activeConversationId.value
  const previousPage = historyPreviousPage.value
  if (!conversationId || !previousPage || historyLoading.value) return

  const previousHeight = chatBox.value?.scrollHeight || 0
  historyLoading.value = true
  try {
    const page = await listConversationMessages(knowledgeId, conversationId, previousPage, 50)
    if (conversationId !== activeConversationId.value) return
    const existingIds = new Set(messages.value.flatMap((message) => (message.id ? [message.id] : [])))
    messages.value = [
      ...page.items.filter((message) => !existingIds.has(message.id)).map(toChatMessage),
      ...messages.value,
    ]
    historyHasPrevious.value = page.has_previous
    historyPreviousPage.value = page.previous_page
    await nextTick()
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight - previousHeight
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    historyLoading.value = false
  }
}

async function startNewConversation() {
  if (sending.value) return
  historyRequestSequence += 1
  activeConversationId.value = null
  activeConversation.value = null
  messages.value = []
  agentRequestSequence += 1
  historyLoading.value = false
  historyHasPrevious.value = false
  historyPreviousPage.value = null
  conversationDrawerVisible.value = false
  await setConversationQuery(null)
}

async function renameConversation(conversation: ConversationItem) {
  if (sending.value || renamingConversationId.value !== null) return
  let nextTitle: string
  try {
    const result = await ElMessageBox.prompt('请输入新的会话标题', '修改会话标题', {
      inputValue: conversation.title,
      inputPlaceholder: '最多100个字符',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (value) => {
        const title = value.trim()
        if (!title) return '会话标题不能为空'
        if (title.length > 100) return '会话标题不能超过100个字符'
        return true
      },
    })
    nextTitle = result.value.trim()
  } catch {
    return
  }

  renamingConversationId.value = conversation.id
  try {
    const updated = await updateConversationTitle(knowledgeId, conversation.id, nextTitle)
    const index = conversations.value.findIndex((item) => item.id === conversation.id)
    if (index >= 0) conversations.value.splice(index, 1, updated)
    if (activeConversationId.value === conversation.id) activeConversation.value = updated
    ElMessage.success('会话标题已修改')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    renamingConversationId.value = null
  }
}

async function removeConversation(conversation: ConversationItem) {
  if (sending.value || deletingConversationId.value !== null) return
  try {
    await ElMessageBox.confirm(
      `确认删除会话“${conversation.title}”吗？该会话中的全部消息都会被删除。`,
      '删除会话',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingConversationId.value = conversation.id
  try {
    await deleteConversation(knowledgeId, conversation.id)
    if (activeConversationId.value === conversation.id) await startNewConversation()
    if (conversations.value.length === 1 && conversationPage.value > 1) {
      conversationPage.value -= 1
    }
    await loadConversations(conversationPage.value)
    ElMessage.success('会话及其消息已删除')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    deletingConversationId.value = null
  }
}

async function restoreConversationFromUrl() {
  const rawValue = Array.isArray(route.query.conversation)
    ? route.query.conversation[0]
    : route.query.conversation
  if (!rawValue) return
  const conversationId = Number(rawValue)
  if (!Number.isInteger(conversationId) || conversationId <= 0) {
    await setConversationQuery(null)
    return
  }
  await selectConversation(conversationId, false)
}

async function scrollBottom() {
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}

async function send() {
  const question = input.value.trim()
  if (!question || sending.value) return
  if (agentEnabled.value && !agentConfig.value?.chat_model_status.available) {
    return ElMessage.warning('Agent模式需要可用的Chat模型，请先配置模型')
  }
  if (!agentEnabled.value && !hasDocuments.value) {
    return ElMessage.warning('请先上传并成功处理一个文档')
  }
  const requestSequence = ++agentRequestSequence
  messages.value.push({ role: 'user', content: question, references: [] })
  const answer: ChatMessage = { role: 'assistant', content: '', references: [] }
  messages.value.push(answer)
  input.value = ''
  sending.value = true
  await scrollBottom()
  try {
    await streamChat(knowledgeId, question, activeConversationId.value, (event, data) => {
      if (requestSequence !== agentRequestSequence) return
      if (event === 'meta' && isRecord(data) && typeof data.conversation_id === 'number') {
        activeConversationId.value = data.conversation_id
        activeConversation.value = null
        void setConversationQuery(data.conversation_id).catch(() => undefined)
      }
      if (event === 'content' && isRecord(data) && typeof data.content === 'string') {
        answer.content += data.content
      }
      if (event === 'agent_start' && isRecord(data) && typeof data.agent_run_id === 'number') {
        ensureLiveTrace(answer, data.agent_run_id)
      }
      if (event === 'agent_step' && isRecord(data) && typeof data.step === 'number' && answer.agentTrace) {
        answer.agentTrace.step_count = data.step
      }
      if (
        event === 'tool_start' &&
        isRecord(data) &&
        typeof data.execution_id === 'number' &&
        typeof data.step === 'number' &&
        typeof data.sequence === 'number' &&
        typeof data.tool_name === 'string' &&
        isRecord(data.arguments) &&
        answer.agentTrace
      ) {
        updateLiveTool(answer.agentTrace, {
          id: data.execution_id,
          step: data.step,
          sequence: data.sequence,
          tool_call_id: '',
          tool_name: data.tool_name,
          arguments: data.arguments,
          result_summary: '',
          result_payload: {},
          status: 'RUNNING',
          latency_ms: 0,
          error_code: '',
          error_message: '',
        })
      }
      if (
        event === 'tool_result' &&
        isRecord(data) &&
        typeof data.execution_id === 'number' &&
        typeof data.step === 'number' &&
        typeof data.sequence === 'number' &&
        typeof data.tool_name === 'string' &&
        typeof data.result_summary === 'string' &&
        typeof data.latency_ms === 'number' &&
        isRecord(data.result_payload) &&
        answer.agentTrace
      ) {
        const existing = answer.agentTrace.tool_executions.find(
          (item) => item.id === data.execution_id,
        )
        updateLiveTool(answer.agentTrace, {
          id: data.execution_id,
          step: data.step,
          sequence: data.sequence,
          tool_call_id: existing?.tool_call_id || '',
          tool_name: data.tool_name,
          arguments: existing?.arguments || {},
          result_summary: data.result_summary,
          result_payload: data.result_payload,
          status: 'SUCCESS',
          latency_ms: data.latency_ms,
          error_code: '',
          error_message: '',
        })
      }
      if (
        event === 'tool_error' &&
        isRecord(data) &&
        typeof data.execution_id === 'number' &&
        typeof data.step === 'number' &&
        typeof data.sequence === 'number' &&
        typeof data.tool_name === 'string' &&
        typeof data.error_code === 'string' &&
        typeof data.error_message === 'string' &&
        typeof data.latency_ms === 'number' &&
        answer.agentTrace
      ) {
        const existing = answer.agentTrace.tool_executions.find(
          (item) => item.id === data.execution_id,
        )
        updateLiveTool(answer.agentTrace, {
          id: data.execution_id,
          step: data.step,
          sequence: data.sequence,
          tool_call_id: existing?.tool_call_id || '',
          tool_name: data.tool_name,
          arguments: existing?.arguments || {},
          result_summary: '',
          result_payload: {},
          status: data.status === 'REJECTED' ? 'REJECTED' : 'FAILURE',
          latency_ms: data.latency_ms,
          error_code: data.error_code,
          error_message: data.error_message,
        })
      }
      if (
        event === 'agent_done' &&
        isRecord(data) &&
        typeof data.agent_run_id === 'number' &&
        typeof data.status === 'string'
      ) {
        const trace = ensureLiveTrace(answer, data.agent_run_id)
        if (['SUCCESS', 'FAILURE', 'LIMIT_REACHED', 'CANCELLED'].includes(data.status)) {
          trace.status = data.status as AgentRunStatus
        }
        if (typeof data.step_count === 'number') trace.step_count = data.step_count
        if (typeof data.error_code === 'string') trace.error_code = data.error_code
        if (typeof data.error_message === 'string') trace.error_message = data.error_message
      }
      if (event === 'references' && isReferenceItems(data)) answer.references = data
      if (event === 'error' && isRecord(data) && typeof data.message === 'string') {
        throw new Error(data.message)
      }
      scrollBottom()
    })
  } catch (error) {
    if (requestSequence === agentRequestSequence) {
      answer.content += `\n\n请求失败：${errorMessage(error)}`
    }
  } finally {
    if (requestSequence === agentRequestSequence) {
      sending.value = false
      await loadConversations(1)
      await scrollBottom()
    }
  }
}

function renderMarkdown(content: string) {
  return marked.parse(content) as string
}

function agentStatusText(status: AgentRunStatus) {
  return {
    RUNNING: '执行中',
    SUCCESS: '执行成功',
    FAILURE: '执行失败',
    LIMIT_REACHED: '达到最大执行步骤',
    CANCELLED: '执行已取消',
  }[status]
}

function agentStatusType(status: AgentRunStatus): 'info' | 'success' | 'danger' | 'warning' {
  return {
    RUNNING: 'info',
    SUCCESS: 'success',
    FAILURE: 'danger',
    LIMIT_REACHED: 'warning',
    CANCELLED: 'warning',
  }[status] as 'info' | 'success' | 'danger' | 'warning'
}

function toolStatusText(status: ToolExecutionStatus) {
  return {
    RUNNING: '执行中',
    SUCCESS: '成功',
    FAILURE: '失败',
    REJECTED: '已拒绝',
  }[status]
}

function toolStatusType(status: ToolExecutionStatus): 'info' | 'success' | 'danger' | 'warning' {
  return {
    RUNNING: 'info',
    SUCCESS: 'success',
    FAILURE: 'danger',
    REJECTED: 'warning',
  }[status] as 'info' | 'success' | 'danger' | 'warning'
}

function safeJson(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2)
}

function ensureLiveTrace(answer: ChatMessage, agentRunId: number): AgentRunTrace {
  if (!answer.agentTrace || answer.agentTrace.id !== agentRunId) {
    answer.agentTrace = {
      id: agentRunId,
      status: 'RUNNING',
      step_count: 0,
      error_code: '',
      error_message: '',
      tool_executions: [],
    }
  }
  return answer.agentTrace
}

function updateLiveTool(trace: AgentRunTrace, execution: ToolExecutionItem) {
  const index = trace.tool_executions.findIndex((item) => item.id === execution.id)
  if (index >= 0) trace.tool_executions.splice(index, 1, execution)
  else trace.tool_executions.push(execution)
}

onMounted(async () => {
  await Promise.all([
    loadKnowledgeBase(),
    loadDocuments(),
    loadConversations(1),
    loadModelStatus(false),
    loadRetrievalConfig(false),
    loadAgentConfig(false),
    loadProcessingTasks(),
  ])
  await restoreConversationFromUrl()
})

onBeforeUnmount(() => {
  pollRequestSequence += 1
  retrievalDebugRequestSequence += 1
  retrievalCompareRequestSequence += 1
  chunkRequestSequence += 1
  stopTaskPolling()
})
</script>

<template>
  <div class="detail-page">
    <aside class="document-panel">
      <div class="detail-brand"><BrandLogo /><span><i />知识空间在线</span></div>
      <button class="back-button" @click="router.push('/knowledge')"><el-icon><ArrowLeft /></el-icon>返回知识空间</button>
      <div class="panel-title"><span>KNOWLEDGE BASE</span><div><h2>{{ knowledgeBase?.name || '文档' }}</h2><p>支持 TXT、MD、PDF、DOCX，最大10MB</p></div></div>
      <label class="upload-button" :class="{ disabled: uploading }">
        <input type="file" accept=".txt,.md,.pdf,.docx" :disabled="uploading" @change="chooseFile" />
        <el-icon><UploadFilled /></el-icon>{{ uploading ? '正在上传...' : '上传文档' }}
      </label>
      <div class="document-list">
        <div v-for="item in documents" :key="item.id" class="document-row">
          <div class="document-summary">
            <el-icon class="document-icon"><Document /></el-icon>
            <div class="document-info">
              <strong :title="item.name">{{ item.name }}</strong>
              <div class="document-meta">
                <el-tag size="small" effect="light" :type="statusTagType(item.status)">{{ statusText(item.status) }}</el-tag>
                <span>{{ item.parent_chunk_count }} Parent / {{ item.paragraph_count }} Child</span>
              </div>
              <el-tag v-if="item.needs_reprocess" class="document-stale-tag" size="small" type="warning">向量需更新</el-tag>
            </div>
          </div>
          <p v-if="item.error_message" class="document-error">{{ item.error_message }}</p>
          <div v-if="taskForDocument(item.id)" class="document-task">
            <div class="document-task-head">
              <el-tag size="small" :type="taskTagType(taskForDocument(item.id)!.status)">
                {{ taskStatusText(taskForDocument(item.id)!.status) }}
              </el-tag>
              <span>{{ taskStageText(taskForDocument(item.id)!.current_stage) }}</span>
              <span>第 {{ taskForDocument(item.id)!.attempt_count }} 次尝试</span>
            </div>
            <el-progress
              :percentage="taskForDocument(item.id)!.progress"
              :status="taskForDocument(item.id)!.status === 'SUCCESS' ? 'success' : undefined"
              :stroke-width="6"
            />
            <p v-if="taskForDocument(item.id)!.error_message" class="document-error">
              {{ taskForDocument(item.id)!.error_message }}
            </p>
          </div>
          <div class="document-actions">
            <el-button text size="small" :icon="ViewIcon" @click="openParagraphs(item)">查看切片</el-button>
            <el-button text size="small" :icon="Setting" @click="openChunkSettings(item)">切片设置</el-button>
            <el-button
              v-if="taskForDocument(item.id)?.status === 'FAILURE' || taskForDocument(item.id)?.status === 'ENQUEUE_FAILED' || taskForDocument(item.id)?.status === 'CANCELLED'"
              text size="small" type="warning" :icon="Refresh"
              :loading="retryingTaskId === taskForDocument(item.id)?.id"
              :disabled="retryingTaskId !== null || cancellingTaskId !== null"
              @click="retryTask(taskForDocument(item.id)!)"
            >重试任务</el-button>
            <el-button
              v-if="isTaskActive(taskForDocument(item.id))"
              text size="small" type="warning"
              :loading="cancellingTaskId === taskForDocument(item.id)?.id"
              :disabled="cancellingTaskId !== null"
              @click="cancelTask(taskForDocument(item.id)!)"
            >取消处理</el-button>
            <el-button text size="small" :icon="Refresh" :loading="reprocessingId === item.id" :disabled="reprocessingId !== null || deletingId !== null || isTaskActive(taskForDocument(item.id))" @click="reprocess(item)">重新处理</el-button>
            <el-button text size="small" type="danger" :icon="Delete" :loading="deletingId === item.id" :disabled="deletingId !== null || reprocessingId !== null" @click="removeDocument(item)">删除</el-button>
          </div>
        </div>
        <p v-if="!documents.length" class="empty-text">还没有文档</p>
      </div>
    </aside>

    <main class="chat-panel">
      <header class="chat-header">
        <div>
          <span class="chat-context-label">KNOWLEDGE ASSISTANT</span>
          <h1>{{ knowledgeBase?.name || '知识库问答' }}</h1>
          <p>{{ activeConversationTitle }} · {{ knowledgeBase?.description || '答案将尽量依据左侧上传的资料生成' }}</p>
        </div>
        <div class="chat-header-actions">
          <el-button :loading="agentConfigLoading" @click="openAgentSettings">Agent 设置</el-button>
          <el-button :icon="Setting" :loading="modelStatusLoading" @click="openModelSettings">模型设置</el-button>
          <el-button @click="openRetrievalSettings">检索设置</el-button>
          <el-button @click="openRetrievalDebug">检索调试</el-button>
          <el-button :icon="ChatLineRound" @click="openConversationHistory">历史会话</el-button>
          <el-tag :type="agentEnabled ? 'warning' : 'info'">{{ agentEnabled ? 'Agent 模式' : '普通 RAG' }}</el-tag>
          <el-tag :type="modelStatus?.chat.source === 'LOCAL' ? 'info' : 'success'">{{ modelStatus?.chat.label || '模型状态' }}</el-tag>
        </div>
      </header>
      <section ref="chatBox" v-loading="historyLoading" class="chat-messages">
        <div v-if="historyHasPrevious" class="older-message-wrap">
          <el-button size="small" :loading="historyLoading" @click="loadOlderMessages">加载更早消息</el-button>
        </div>
        <div v-if="!messages.length" class="chat-welcome">
          <div class="welcome-symbol">枢</div><span>TRUSTED ANSWERS</span><h2>从企业知识中寻找可靠答案</h2><p>上传文档或选择历史会话，每条回答都会尽量附带可追溯的引用。</p>
        </div>
        <article v-for="(message, index) in messages" :key="message.id || index" class="message" :class="message.role">
          <div class="message-avatar">{{ message.role === 'user' ? '你' : 'AI' }}</div>
          <div class="message-body">
            <div v-if="message.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(message.content || '正在思考…')" />
            <div v-else>{{ message.content }}</div>
            <details
              v-if="message.agentTrace"
              class="agent-trace"
              :open="message.agentTrace.status === 'RUNNING'"
            >
              <summary>
                <span>Agent 执行轨迹</span>
                <el-tag size="small" :type="agentStatusType(message.agentTrace.status)">
                  {{ agentStatusText(message.agentTrace.status) }}
                </el-tag>
                <small>{{ message.agentTrace.step_count }} 个步骤</small>
              </summary>
              <p v-if="message.agentTrace.error_message" class="agent-trace-error">
                {{ message.agentTrace.error_message }}
              </p>
              <el-empty
                v-if="!message.agentTrace.tool_executions.length"
                :image-size="48"
                description="本次回答未调用工具"
              />
              <article
                v-for="execution in message.agentTrace.tool_executions"
                :key="execution.id"
                class="tool-execution"
              >
                <header>
                  <strong>步骤 {{ execution.step }}.{{ execution.sequence }} · {{ execution.tool_name }}</strong>
                  <span>
                    <el-tag size="small" :type="toolStatusType(execution.status)">
                      {{ toolStatusText(execution.status) }}
                    </el-tag>
                    {{ execution.latency_ms }} ms
                  </span>
                </header>
                <div class="tool-execution-block">
                  <span>安全参数</span>
                  <pre>{{ safeJson(execution.arguments) }}</pre>
                </div>
                <p v-if="execution.result_summary" class="tool-result-summary">
                  {{ execution.result_summary }}
                </p>
                <p v-if="execution.error_message" class="agent-trace-error">
                  {{ execution.error_message }}
                </p>
              </article>
            </details>
            <div v-if="message.references?.length" class="references">
              <strong>引用资料</strong>
              <details v-for="(reference, refIndex) in message.references" :key="refIndex">
                <summary>[{{ refIndex + 1 }}] {{ reference.document_name }} · 相似度 {{ reference.similarity }}</summary>
                <p>{{ reference.content }}</p>
              </details>
            </div>
          </div>
        </article>
      </section>
      <footer class="chat-input-wrap">
        <div class="chat-input">
          <el-input v-model="input" type="textarea" :rows="2" resize="none" placeholder="向知枢提问，Ctrl + Enter 发送" @keydown.ctrl.enter.prevent="send" />
          <el-button type="primary" :loading="sending" @click="send">发送问题</el-button>
        </div>
        <p>{{ agentEnabled ? 'Agent 模式已开启' : '普通 RAG 模式' }} · {{ modelSummary }}<span v-if="modelStatus?.embedding_stale_document_count"> · {{ modelStatus.embedding_stale_document_count }} 个文档需要重新处理</span></p>
      </footer>
    </main>

    <el-drawer v-model="agentSettingsVisible" size="min(600px, 94vw)">
      <template #header>
        <div class="agent-settings-title">
          <strong>Agent 设置</strong>
          <span>控制当前知识库是否允许模型调用后端安全工具</span>
        </div>
      </template>
      <div v-loading="agentConfigLoading" class="agent-settings-form">
        <el-alert
          v-if="agentConfig && !agentConfig.chat_model_status.available"
          type="warning"
          :closable="false"
          title="当前没有可用的 Chat 模型；开启 Agent 后需要先配置模型才能发送。"
        />
        <el-form label-position="top">
          <el-form-item label="Agent 模式">
            <el-switch
              v-model="agentForm.agent_enabled"
              active-text="开启"
              inactive-text="关闭，使用普通 RAG"
            />
          </el-form-item>
          <el-form-item label="当前 Chat 模型">
            <div class="agent-model-line">
              <el-tag :type="agentConfig?.chat_model_status.available ? 'success' : 'info'">
                {{ agentConfig?.chat_model_status.label || '未读取' }}
              </el-tag>
              <span>{{ agentConfig?.chat_model_status.source || 'LOCAL' }}</span>
            </div>
          </el-form-item>
          <el-form-item label="最大执行步骤">
            <el-input-number v-model="agentForm.agent_max_steps" :min="1" :max="10" />
            <p class="field-help">达到上限后会安全停止，不会把未完成内容保存成成功回答。</p>
          </el-form-item>
          <el-form-item label="允许使用的工具">
            <el-checkbox-group v-model="agentForm.enabled_tools" class="agent-tool-options">
              <el-checkbox
                v-for="tool in agentConfig?.available_tools || []"
                :key="tool.name"
                :value="tool.name"
              >
                <span class="agent-tool-label"><strong>{{ tool.label }}</strong>{{ tool.description }}</span>
              </el-checkbox>
            </el-checkbox-group>
            <p class="field-help">不勾选表示没有工具权限，Agent只能直接回答。</p>
          </el-form-item>
          <el-form-item label="Agent System Prompt">
            <el-input
              v-model="agentForm.agent_system_prompt"
              type="textarea"
              :rows="5"
              maxlength="2000"
              show-word-limit
              placeholder="留空使用系统默认；这里仅补充业务风格，不能覆盖固定安全约束"
            />
          </el-form-item>
        </el-form>
        <el-alert
          type="info"
          :closable="false"
          title="页面只展示实际工具调用记录，不展示模型隐藏思维链。"
        />
      </div>
      <template #footer>
        <el-button :disabled="agentConfigSaving" @click="resetAgentDefaults">恢复默认值</el-button>
        <el-button :disabled="agentConfigSaving" @click="agentSettingsVisible = false">取消</el-button>
        <el-button type="primary" :loading="agentConfigSaving" @click="saveAgentSettings">保存设置</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="retrievalSettingsVisible" size="min(600px, 94vw)">
      <template #header>
        <div class="retrieval-drawer-title">
          <strong>知识库检索设置</strong>
          <span>控制正式搜索、问答引用和发送给模型的上下文</span>
        </div>
      </template>
      <div v-loading="retrievalConfigLoading" class="retrieval-settings-form">
        <el-form label-position="top">
          <el-form-item label="检索模式">
            <el-radio-group v-model="retrievalForm.retrieval_mode">
              <el-radio-button value="VECTOR">纯向量</el-radio-button>
              <el-radio-button value="HYBRID">混合检索</el-radio-button>
            </el-radio-group>
            <p class="field-help">混合检索同时考虑语义相似度和关键词 BM25 分数。</p>
          </el-form-item>
          <el-form-item v-if="retrievalForm.retrieval_mode === 'HYBRID'" label="融合方式">
            <el-radio-group v-model="retrievalForm.fusion_method">
              <el-radio-button value="WEIGHTED">分数加权</el-radio-button>
              <el-radio-button value="RRF">RRF 排名融合</el-radio-button>
            </el-radio-group>
            <p class="field-help">RRF分别截取向量和BM25候选，再按两个排名融合，避免直接比较不同量纲的分数。</p>
          </el-form-item>
          <div class="retrieval-number-grid">
            <el-form-item label="返回切片数量">
              <el-input-number v-model="retrievalForm.retrieval_top_k" :min="1" :max="20" />
            </el-form-item>
            <el-form-item label="最大上下文字符数">
              <el-input-number v-model="retrievalForm.max_context_chars" :min="1000" :max="30000" :step="500" />
            </el-form-item>
          </div>
          <el-form-item :label="`最低相关度阈值：${retrievalForm.similarity_threshold.toFixed(2)}`">
            <el-slider v-model="retrievalForm.similarity_threshold" :min="0" :max="1" :step="0.05" show-stops />
            <p class="field-help">最终分低于该值的切片不会进入问答上下文。</p>
          </el-form-item>
          <el-form-item v-if="retrievalForm.fusion_method === 'WEIGHTED'" :label="`向量权重：${retrievalForm.vector_weight.toFixed(2)}（关键词权重：${keywordWeight.toFixed(2)}）`">
            <el-slider v-model="retrievalForm.vector_weight" :min="0" :max="1" :step="0.05" show-stops />
            <p class="field-help">纯向量模式忽略关键词权重；混合模式下两项权重之和始终为 1。</p>
          </el-form-item>
          <template v-if="retrievalForm.retrieval_mode === 'HYBRID' && retrievalForm.fusion_method === 'RRF'">
            <div class="retrieval-number-grid">
              <el-form-item label="向量召回候选">
                <el-input-number v-model="retrievalForm.vector_candidate_k" :min="1" :max="100" />
              </el-form-item>
              <el-form-item label="BM25召回候选">
                <el-input-number v-model="retrievalForm.keyword_candidate_k" :min="1" :max="100" />
              </el-form-item>
              <el-form-item label="RRF平滑参数">
                <el-input-number v-model="retrievalForm.rrf_k" :min="1" :max="200" />
              </el-form-item>
              <el-form-item label="重排候选数量">
                <el-input-number v-model="retrievalForm.rerank_candidate_k" :min="1" :max="50" />
              </el-form-item>
            </div>
            <el-form-item label="Cross-Encoder精排">
              <el-switch
                v-model="retrievalForm.rerank_enabled"
                :disabled="retrievalCapabilitiesLoading || !retrievalCapabilities?.reranker_ready"
                active-text="启用"
                inactive-text="关闭"
              />
              <p class="field-help">
                {{ retrievalCapabilities?.reranker_ready
                  ? `当前模型：${retrievalCapabilities.reranker_model}（${retrievalCapabilities.device}）`
                  : '服务器尚未准备本地重排模型；RRF仍可正常使用。' }}
              </p>
            </el-form-item>
            <el-alert
              v-if="retrievalForm.rerank_enabled && !retrievalCapabilities?.reranker_ready"
              type="warning"
              :closable="false"
              title="当前配置会在运行时安全降级到RRF；请先准备本地Cross-Encoder。"
            />
          </template>
          <el-form-item label="System Prompt">
            <el-input
              v-model="retrievalForm.system_prompt"
              type="textarea"
              :rows="4"
              maxlength="2000"
              show-word-limit
              placeholder="留空使用系统默认：仅根据资料回答并标注引用"
            />
          </el-form-item>
          <el-form-item label="资料不足提示">
            <el-input
              v-model="retrievalForm.no_answer_message"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="留空使用系统默认提示"
            />
          </el-form-item>
        </el-form>
        <el-alert
          type="info"
          :closable="false"
          title="设置只影响当前知识库；恢复默认值后仍需点击保存。"
        />
      </div>
      <template #footer>
        <el-button :disabled="retrievalConfigSaving" @click="resetRetrievalDefaults">恢复默认值</el-button>
        <el-button :disabled="retrievalConfigSaving" @click="retrievalSettingsVisible = false">取消</el-button>
        <el-button type="primary" :loading="retrievalConfigSaving" @click="saveRetrievalSettings">保存设置</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="retrievalDebugVisible" size="min(820px, 96vw)">
      <template #header>
        <div class="retrieval-drawer-title">
          <strong>检索调试工作台</strong>
          <span>只执行检索，不调用 Chat 模型，也不会创建会话</span>
        </div>
      </template>
      <div class="retrieval-debug-toolbar">
        <el-input
          v-model="retrievalQuery"
          type="textarea"
          :rows="2"
          maxlength="1000"
          placeholder="输入一个问题，查看各切片的评分和排除原因"
          @keydown.ctrl.enter.prevent="runRetrievalDebug"
        />
        <el-button type="primary" :loading="retrievalDebugLoading" @click="runRetrievalDebug">执行检索</el-button>
        <el-button :loading="retrievalCompareLoading" @click="runRetrievalCompare">A/B 对比</el-button>
      </div>
      <div v-loading="retrievalDebugLoading || retrievalCompareLoading" class="retrieval-debug-content">
        <el-empty v-if="!retrievalDebugLoading && !retrievalCompareLoading && !retrievalDebugResult && !retrievalCompareResult" description="输入问题后执行检索或A/B策略对比" />
        <template v-if="retrievalDebugResult">
          <div class="retrieval-debug-summary">
            <div><span>模式</span><strong>{{ retrievalDebugResult.effective_config.mode }} / {{ retrievalDebugResult.effective_config.fusion_method }}</strong></div>
            <div><span>候选</span><strong>{{ retrievalDebugResult.candidate_count }}</strong></div>
            <div><span>入选</span><strong>{{ retrievalDebugResult.selected_count }}</strong></div>
            <div><span>上下文字符</span><strong>{{ retrievalDebugResult.context_chars }}</strong></div>
            <div><span>耗时</span><strong>{{ retrievalDebugResult.latency_ms }} ms</strong></div>
          </div>
          <p class="retrieval-effective-line">
            top_k {{ retrievalDebugResult.effective_config.top_k }} · 阈值 {{ scoreText(retrievalDebugResult.effective_config.threshold) }} ·
            向量权重 {{ scoreText(retrievalDebugResult.effective_config.vector_weight) }} · 关键词权重 {{ scoreText(retrievalDebugResult.effective_config.keyword_weight) }}
            · 准备 {{ retrievalDebugResult.stage_timings.preparation_ms }}ms · 排序 {{ retrievalDebugResult.stage_timings.ranking_ms }}ms · 重排 {{ retrievalDebugResult.stage_timings.rerank_ms }}ms
          </p>
          <el-alert
            v-if="retrievalDebugResult.rerank_fallback_reason"
            type="warning"
            :closable="false"
            :title="retrievalDebugResult.rerank_fallback_reason"
          />
          <el-empty v-if="!retrievalDebugResult.items.length" description="没有状态成功且向量版本兼容的候选切片" />
          <article v-for="item in retrievalDebugResult.items" :key="item.paragraph_id" class="retrieval-candidate">
            <div class="retrieval-candidate-head">
              <div>
                <strong>{{ item.document_name }} · 切片 #{{ item.position }}</strong>
                <span>Paragraph {{ item.paragraph_id }}<template v-if="item.parent_position"> · Parent #{{ item.parent_position }}</template></span>
              </div>
              <el-tag :type="item.included ? 'success' : 'info'">
                {{ item.included ? '进入上下文' : item.exclusion_reason }}
              </el-tag>
            </div>
            <div class="retrieval-scores">
              <span>向量分 / 排名 <strong>{{ scoreText(item.vector_score_normalized) }} / {{ item.vector_rank ?? '—' }}</strong></span>
              <span>BM25分 / 排名 <strong>{{ scoreText(item.keyword_score) }} / {{ item.keyword_rank ?? '—' }}</strong></span>
              <span>RRF分 / 排名 <strong>{{ scoreText(item.rrf_score_normalized) }} / {{ item.rrf_rank ?? '—' }}</strong></span>
              <span>重排分 / 排名 <strong>{{ scoreText(item.rerank_score) }} / {{ item.rerank_rank ?? '—' }}</strong></span>
              <span>重排前排名 <strong>{{ item.pre_rerank_rank ?? '—' }}</strong></span>
              <span>最终排名 <strong>{{ item.final_rank ?? '—' }}</strong></span>
              <span>加权分 <strong>{{ scoreText(item.weighted_score) }}</strong></span>
              <span>最终分 <strong>{{ scoreText(item.final_score) }}</strong></span>
            </div>
            <el-tag v-if="item.content_truncated" size="small" type="warning">Prompt 中正文已按预算截断</el-tag>
            <details class="retrieval-content-details">
              <summary>展开查看命中的Child正文</summary>
              <p>{{ item.content }}</p>
            </details>
            <details v-if="item.parent_content" class="retrieval-content-details">
              <summary>展开查看生成阶段使用的Parent正文</summary>
              <p>{{ item.parent_content }}</p>
            </details>
          </article>
        </template>
        <section v-if="retrievalCompareResult" class="retrieval-compare">
          <header>
            <div><strong>当前策略</strong><span>{{ retrievalCompareResult.baseline.effective_config.mode }} / {{ retrievalCompareResult.baseline.effective_config.fusion_method }}</span></div>
            <div><strong>实验策略</strong><span>{{ retrievalCompareResult.experimental.effective_config.mode }} / {{ retrievalCompareResult.experimental.effective_config.fusion_method }}</span></div>
          </header>
          <el-alert
            v-if="retrievalCompareResult.experimental.rerank_fallback_reason"
            type="warning"
            :closable="false"
            :title="retrievalCompareResult.experimental.rerank_fallback_reason"
          />
          <div class="retrieval-compare-list">
            <div v-for="change in retrievalCompareResult.changes" :key="change.paragraph_id" :class="['retrieval-rank-change', change.change_type.toLowerCase()]">
              <strong>Paragraph {{ change.paragraph_id }}</strong>
              <span>当前 {{ change.baseline_rank ?? '未召回' }} → 实验 {{ change.experimental_rank ?? '未召回' }}</span>
              <el-tag size="small" :type="change.change_type === 'ADDED' ? 'success' : change.change_type === 'REMOVED' ? 'danger' : 'info'">{{ change.change_type }}</el-tag>
            </div>
          </div>
          <p class="field-help">A/B只对本次问题生效，不保存实验参数，也不会创建会话。</p>
        </section>
      </div>
    </el-drawer>

    <el-drawer v-model="modelSettingsVisible" size="min(520px, 92vw)">
      <template #header>
        <div class="model-settings-title">
          <strong>知识库模型设置</strong>
          <span>分别选择问答模型和文档向量模型</span>
        </div>
      </template>
      <div v-loading="modelStatusLoading" class="model-settings-form">
        <el-alert
          v-if="modelStatus?.embedding_stale_document_count"
          type="warning"
          :closable="false"
          :title="`${modelStatus.embedding_stale_document_count} 个文档的向量与当前 Embedding 配置不一致，请保存后重新处理文档。`"
        />
        <el-form label-position="top">
          <el-form-item label="Chat 模型">
            <el-select v-model="selectedChatConfigId" class="full-button" placeholder="系统默认">
              <el-option label="系统默认（环境变量或本地演示）" :value="null" />
              <el-option v-for="item in chatConfigs" :key="item.id" :label="`${item.name} / ${item.model_name}`" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="Embedding 模型">
            <el-select v-model="selectedEmbeddingConfigId" class="full-button" placeholder="系统默认">
              <el-option label="系统默认（环境变量或本地哈希）" :value="null" />
              <el-option v-for="item in embeddingConfigs" :key="item.id" :label="`${item.name} / ${item.model_name}`" :value="item.id" />
            </el-select>
          </el-form-item>
        </el-form>
        <div v-if="modelStatus" class="effective-model-status">
          <p><strong>当前 Chat：</strong>{{ modelStatus.chat.label }}<span v-if="modelStatus.chat.model_name"> / {{ modelStatus.chat.model_name }}</span></p>
          <p><strong>当前 Embedding：</strong>{{ modelStatus.embedding.label }}<span v-if="modelStatus.embedding.model_name"> / {{ modelStatus.embedding.model_name }}</span></p>
        </div>
        <el-button text type="primary" @click="router.push('/model-configs')">管理 OpenAI 兼容模型配置</el-button>
      </div>
      <template #footer>
        <el-button :disabled="modelSettingsSaving" @click="modelSettingsVisible = false">取消</el-button>
        <el-button type="primary" :loading="modelSettingsSaving" @click="saveModelSettings">保存选择</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="conversationDrawerVisible" size="min(440px, 92vw)">
      <template #header>
        <div class="conversation-drawer-title">
          <strong>历史会话</strong>
          <span>选择会话可以恢复之前的消息</span>
        </div>
      </template>
      <el-button
        class="new-conversation-button"
        type="primary"
        plain
        :icon="Plus"
        :disabled="sending"
        @click="startNewConversation"
      >新建对话</el-button>
      <div v-loading="conversationLoading" class="conversation-list">
        <el-empty v-if="!conversationLoading && !conversations.length" description="还没有历史会话" />
        <div
          v-for="conversation in conversations"
          :key="conversation.id"
          class="conversation-row"
          :class="{ active: activeConversationId === conversation.id }"
        >
          <button class="conversation-select" @click="selectConversation(conversation.id)">
            <span class="conversation-summary">
              <strong :title="conversation.title">{{ conversation.title }}</strong>
              <span>{{ conversation.message_count }} 条消息 · {{ new Date(conversation.last_message_at || conversation.created_at).toLocaleString() }}</span>
            </span>
          </button>
          <div class="conversation-actions">
            <el-button
              text
              size="small"
              :icon="EditPen"
              :loading="renamingConversationId === conversation.id"
              :disabled="sending || renamingConversationId !== null || deletingConversationId !== null"
              aria-label="修改会话标题"
              @click="renameConversation(conversation)"
            />
            <el-button
              text
              size="small"
              type="danger"
              :icon="Delete"
              :loading="deletingConversationId === conversation.id"
              :disabled="sending || deletingConversationId !== null || renamingConversationId !== null"
              aria-label="删除会话"
              @click="removeConversation(conversation)"
            />
          </div>
        </div>
      </div>
      <div v-if="conversationTotal" class="conversation-pagination">
        <el-pagination
          v-model:current-page="conversationPage"
          :page-size="conversationPageSize"
          :total="conversationTotal"
          layout="prev, pager, next"
          :disabled="conversationLoading"
          @current-change="loadConversations"
        />
      </div>
    </el-drawer>

    <el-drawer v-model="chunkSettingsVisible" size="min(860px, 96vw)" destroy-on-close>
      <template #header>
        <div class="paragraph-drawer-title">
          <strong>结构化解析与父子切片</strong>
          <span>{{ chunkConfigDocument?.name }}</span>
        </div>
      </template>
      <div v-loading="chunkConfigLoading" class="chunk-settings-layout">
        <section class="chunk-settings-form">
          <el-form label-position="top">
            <el-form-item label="解析器">
              <el-select v-model="chunkConfigForm.parser_type" class="full-button">
                <el-option label="自动识别" value="AUTO" />
                <el-option label="TXT" value="TXT" />
                <el-option label="Markdown" value="MARKDOWN" />
                <el-option label="PDF" value="PDF" />
                <el-option label="DOCX" value="DOCX" />
              </el-select>
            </el-form-item>
            <el-form-item label="切片策略">
              <el-select v-model="chunkConfigForm.chunk_strategy" class="full-button">
                <el-option label="父子切片（Child检索 / Parent生成）" value="PARENT_CHILD" />
                <el-option label="兼容切片" value="LEGACY" />
              </el-select>
            </el-form-item>
            <el-form-item label="Parent 最大 Token 数">
              <el-input-number v-model="chunkConfigForm.parent_max_tokens" :min="400" :max="4000" :step="100" />
            </el-form-item>
            <el-form-item label="Child 目标 Token 数">
              <el-input-number v-model="chunkConfigForm.child_target_tokens" :min="100" :max="1200" :step="50" />
            </el-form-item>
            <el-form-item label="Child 重叠 Token 数">
              <el-input-number v-model="chunkConfigForm.child_overlap_tokens" :min="0" :max="300" :step="10" />
            </el-form-item>
            <el-form-item label="结构保持">
              <el-checkbox v-model="chunkConfigForm.preserve_tables">尽量保持表格整体</el-checkbox>
              <el-checkbox v-model="chunkConfigForm.preserve_code_blocks">尽量保持代码块整体</el-checkbox>
            </el-form-item>
          </el-form>
          <el-alert
            type="info"
            :closable="false"
            title="修改表单不会改变正式切片；预览不生成Embedding，也不创建后台任务。"
          />
          <div class="chunk-settings-actions">
            <el-button :loading="chunkPreviewLoading" :disabled="chunkReindexing" @click="runChunkPreview">预览切片</el-button>
            <el-button type="primary" :loading="chunkReindexing" :disabled="chunkPreviewLoading" @click="saveChunkConfigAndReindex">保存并重新索引</el-button>
          </div>
        </section>
        <section v-loading="chunkPreviewLoading" class="chunk-preview-tree">
          <el-empty v-if="!chunkPreviewLoading && !chunkPreview" description="点击“预览切片”查看真实文件的解析结果" />
          <template v-if="chunkPreview">
            <div class="chunk-preview-summary">
              <el-tag>{{ chunkPreview.parser_type }}</el-tag>
              <span>{{ chunkPreview.block_count }} Blocks</span>
              <span>{{ chunkPreview.parent_count }} Parent</span>
              <span>{{ chunkPreview.child_count }} Child</span>
            </div>
            <el-alert
              v-for="warning in chunkPreview.warnings"
              :key="warning"
              type="warning"
              :closable="false"
              :title="warning"
            />
            <el-alert v-if="chunkPreview.truncated" type="info" :closable="false" title="预览数量已截断，正式重新索引仍会处理全部内容。" />
            <article v-for="parent in chunkPreview.items" :key="parent.position ?? 'legacy'" class="chunk-parent-card">
              <header>
                <strong>{{ parent.position === null ? 'Legacy切片组' : `Parent #${parent.position}` }}</strong>
                <span>{{ parent.heading_path.join(' > ') || '无标题路径' }}</span>
                <el-tag size="small">{{ parent.structure_type }}</el-tag>
                <span>{{ parent.token_count }} tokens</span>
                <span v-if="parent.page_start">第 {{ parent.page_start }}<template v-if="parent.page_end !== parent.page_start">–{{ parent.page_end }}</template> 页</span>
              </header>
              <p v-if="parent.content" class="chunk-parent-content">{{ parent.content }}</p>
              <div class="chunk-child-list">
                <article v-for="child in parent.children" :key="child.position" class="chunk-child-card">
                  <div>
                    <strong>Child #{{ child.position }}</strong>
                    <span>{{ child.heading_path.join(' > ') || '无标题路径' }}</span>
                    <el-tag size="small" effect="plain">{{ child.structure_type }}</el-tag>
                    <span>{{ child.token_count }} tokens</span>
                  </div>
                  <p>{{ child.content }}</p>
                </article>
              </div>
            </article>
          </template>
        </section>
      </div>
    </el-drawer>

    <el-drawer v-model="paragraphDrawerVisible" size="min(680px, 92vw)" destroy-on-close>
      <template #header>
        <div class="paragraph-drawer-title">
          <strong>文档切片</strong>
          <span>{{ previewDocument?.name }}</span>
        </div>
      </template>
      <div v-loading="paragraphLoading" class="paragraph-preview">
        <el-empty v-if="!paragraphLoading && !paragraphs.length" description="暂无可预览的切片" />
        <article v-for="paragraph in paragraphs" :key="paragraph.id" class="paragraph-card">
          <div class="paragraph-number">
            切片 #{{ paragraph.position }} · {{ paragraph.chunk_type }} · {{ paragraph.token_count }} tokens
            <span v-if="paragraph.parent_position"> · Parent #{{ paragraph.parent_position }}</span>
          </div>
          <div class="paragraph-structure-meta">
            <span>{{ paragraph.heading_path.join(' > ') || '无标题路径' }}</span>
            <span>{{ paragraph.structure_type }}</span>
            <span v-if="paragraph.page_start">第 {{ paragraph.page_start }}<template v-if="paragraph.page_end !== paragraph.page_start">–{{ paragraph.page_end }}</template> 页</span>
          </div>
          <p>{{ paragraph.content }}</p>
        </article>
      </div>
      <template #footer>
        <div class="paragraph-pagination">
          <span>共 {{ paragraphTotal }} 个切片</span>
          <el-pagination
            v-model:current-page="paragraphPage"
            :page-size="paragraphPageSize"
            :total="paragraphTotal"
            layout="prev, pager, next"
            :disabled="paragraphLoading"
            @current-change="loadParagraphs"
          />
        </div>
      </template>
    </el-drawer>
  </div>
</template>
