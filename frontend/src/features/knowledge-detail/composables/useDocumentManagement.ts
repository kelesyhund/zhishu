import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  cancelDocumentProcessingTask,
  deleteDocument,
  getDocumentChunkingConfig,
  listDocumentParagraphs,
  listDocumentProcessingTasks,
  listDocuments,
  previewDocumentChunks,
  reindexDocument,
  reprocessDocument,
  retryDocumentProcessingTask,
  updateDocumentChunkingConfig,
  uploadDocument,
  type DocumentChunkingConfig,
  type DocumentChunkPreview,
  type DocumentItem,
  type DocumentProcessingTaskItem,
  type DocumentTaskStatus,
  type ParagraphItem,
} from '../../../api'
import { errorMessage } from '../../../api/client'
import { useRequestGuard } from './useRequestGuard'

interface DocumentManagementOptions {
  onDocumentsChanged?: () => void | Promise<void>
}

const activeTaskStatuses: DocumentTaskStatus[] = ['PENDING', 'PROCESSING', 'RETRYING', 'CANCEL_REQUESTED']

export function useDocumentManagement(knowledgeId: number, options: DocumentManagementOptions = {}) {
  const documents = ref<DocumentItem[]>([])
  const loading = ref(false)
  const uploading = ref(false)
  const deletingId = ref<number | null>(null)
  const reprocessingId = ref<number | null>(null)
  const processingTasks = ref<DocumentProcessingTaskItem[]>([])
  const taskLoading = ref(false)
  const retryingTaskId = ref<number | null>(null)
  const cancellingTaskId = ref<number | null>(null)
  const paragraphVisible = ref(false)
  const previewDocument = ref<DocumentItem | null>(null)
  const paragraphs = ref<ParagraphItem[]>([])
  const paragraphLoading = ref(false)
  const paragraphPage = ref(1)
  const paragraphPageSize = ref(20)
  const paragraphTotal = ref(0)
  const chunkVisible = ref(false)
  const chunkDocument = ref<DocumentItem | null>(null)
  const chunkLoading = ref(false)
  const chunkPreviewLoading = ref(false)
  const chunkReindexing = ref(false)
  const chunkPreview = ref<DocumentChunkPreview | null>(null)
  const chunkForm = reactive<DocumentChunkingConfig>({
    parser_type: 'AUTO', chunk_strategy: 'PARENT_CHILD', parent_max_tokens: 1500,
    child_target_tokens: 400, child_overlap_tokens: 60, preserve_tables: true, preserve_code_blocks: true,
  })
  const listGuard = useRequestGuard()
  const taskGuard = useRequestGuard()
  const chunkGuard = useRequestGuard()
  const paragraphGuard = useRequestGuard()
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  const activeTasks = computed(() => processingTasks.value.filter((task) => activeTaskStatuses.includes(task.status)))
  const hasDocuments = computed(() => documents.value.some((item) => item.status === 'SUCCESS' && !item.needs_reprocess))

  async function notifyChanged() { await options.onDocumentsChanged?.() }

  async function load(showError = true) {
    const sequence = listGuard.next()
    loading.value = true
    try {
      const value = await listDocuments(knowledgeId)
      if (listGuard.current(sequence)) documents.value = value
    } catch (error) {
      if (listGuard.current(sequence) && showError) ElMessage.error(errorMessage(error))
    } finally { if (listGuard.current(sequence)) loading.value = false }
  }

  function taskForDocument(documentId: number) {
    return processingTasks.value.find((task) => task.document_id === documentId) || null
  }
  function isTaskActive(task: DocumentProcessingTaskItem | null) { return Boolean(task && activeTaskStatuses.includes(task.status)) }
  function upsertTask(task: DocumentProcessingTaskItem) {
    processingTasks.value = [task, ...processingTasks.value.filter((item) => item.id !== task.id)]
  }
  function replaceDocument(document: DocumentItem) {
    const index = documents.value.findIndex((item) => item.id === document.id)
    if (index >= 0) documents.value.splice(index, 1, document)
    if (previewDocument.value?.id === document.id) previewDocument.value = document
  }
  function stopPolling() {
    if (pollTimer !== null) clearTimeout(pollTimer)
    pollTimer = null
  }
  function schedulePoll(delay = 2000) {
    stopPolling()
    pollTimer = setTimeout(() => void loadTasks(true), delay)
  }

  async function loadTasks(fromPoll = false) {
    const sequence = taskGuard.next()
    if (!fromPoll) taskLoading.value = true
    const previous = new Map(processingTasks.value.map((task) => [task.id, task.status] as const))
    try {
      const value = await listDocumentProcessingTasks(knowledgeId, 1, 100)
      if (!taskGuard.current(sequence)) return
      processingTasks.value = value.items
      const becameTerminal = value.items.some((task) => {
        const old = previous.get(task.id)
        return old && activeTaskStatuses.includes(old) && !activeTaskStatuses.includes(task.status)
      })
      if (becameTerminal) { await load(); await notifyChanged() }
      if (activeTasks.value.length) schedulePoll()
      else stopPolling()
    } catch (error) {
      if (!taskGuard.current(sequence)) return
      stopPolling()
      if (!fromPoll) ElMessage.error(errorMessage(error))
    } finally { if (taskGuard.current(sequence)) taskLoading.value = false }
  }

  async function chooseFile(event: Event) {
    const element = event.target as HTMLInputElement
    const file = element.files?.[0]
    if (!file || uploading.value) return
    uploading.value = true
    try {
      const result = await uploadDocument(knowledgeId, file)
      documents.value.unshift(result.document)
      upsertTask(result.task)
      await notifyChanged()
      ElMessage.success('文件上传成功，文档已进入后台处理队列')
      schedulePoll(300)
    } catch (error) { ElMessage.error(errorMessage(error)); await load() }
    finally { uploading.value = false; element.value = '' }
  }

  async function loadParagraphs(page = paragraphPage.value) {
    const document = previewDocument.value
    if (!document) return
    const sequence = paragraphGuard.next()
    paragraphLoading.value = true
    try {
      const value = await listDocumentParagraphs(knowledgeId, document.id, page, paragraphPageSize.value)
      if (!paragraphGuard.current(sequence) || previewDocument.value?.id !== document.id) return
      paragraphs.value = value.items
      paragraphTotal.value = value.total
      paragraphPage.value = value.page
      paragraphPageSize.value = value.page_size
    } catch (error) {
      if (paragraphGuard.current(sequence)) {
        paragraphs.value = []; paragraphTotal.value = 0; ElMessage.error(errorMessage(error))
      }
    } finally { if (paragraphGuard.current(sequence)) paragraphLoading.value = false }
  }

  async function openParagraphs(document: DocumentItem) {
    paragraphGuard.invalidate()
    previewDocument.value = document
    paragraphPage.value = 1
    paragraphs.value = []
    paragraphTotal.value = 0
    paragraphVisible.value = true
    await loadParagraphs(1)
  }

  async function openChunkSettings(document: DocumentItem) {
    const sequence = chunkGuard.next()
    chunkDocument.value = document
    chunkPreview.value = null
    chunkVisible.value = true
    chunkLoading.value = true
    try {
      const value = await getDocumentChunkingConfig(knowledgeId, document.id)
      if (chunkGuard.current(sequence) && chunkDocument.value?.id === document.id) Object.assign(chunkForm, value)
    } catch (error) { if (chunkGuard.current(sequence)) ElMessage.error(errorMessage(error)) }
    finally { if (chunkGuard.current(sequence)) chunkLoading.value = false }
  }

  async function runChunkPreview() {
    const document = chunkDocument.value
    if (!document || chunkPreviewLoading.value) return
    const sequence = chunkGuard.next()
    chunkPreviewLoading.value = true
    chunkPreview.value = null
    try {
      const value = await previewDocumentChunks(knowledgeId, document.id, { ...chunkForm })
      if (chunkGuard.current(sequence) && chunkDocument.value?.id === document.id) chunkPreview.value = value
    } catch (error) { if (chunkGuard.current(sequence)) ElMessage.error(errorMessage(error)) }
    finally { if (chunkGuard.current(sequence)) chunkPreviewLoading.value = false }
  }

  async function saveChunkConfigAndReindex() {
    const document = chunkDocument.value
    if (!document || chunkReindexing.value) return
    try {
      await ElMessageBox.confirm(`确认保存“${document.name}”的切片配置并重新索引吗？这会重新解析文件并计算 Embedding；旧切片会保留到新数据完整生成。`, '保存并重新索引', { type: 'warning', confirmButtonText: '确认重新索引', cancelButtonText: '取消' })
    } catch { return }
    chunkReindexing.value = true
    try {
      const saved = await updateDocumentChunkingConfig(knowledgeId, document.id, { ...chunkForm })
      replaceDocument(saved.document)
      const result = await reindexDocument(knowledgeId, document.id)
      replaceDocument(result.document); upsertTask(result.task); chunkVisible.value = false
      ElMessage.success('切片配置已保存，重新索引任务已进入队列'); schedulePoll(300)
    } catch (error) { ElMessage.error(errorMessage(error)); await load() }
    finally { chunkReindexing.value = false }
  }

  async function reprocess(document: DocumentItem) {
    if (reprocessingId.value !== null) return
    reprocessingId.value = document.id
    try {
      const result = await reprocessDocument(knowledgeId, document.id)
      replaceDocument(result.document); upsertTask(result.task)
      ElMessage.success('重新处理任务已进入队列，旧切片会保留到新处理成功'); schedulePoll(300)
    } catch (error) { await load(); await notifyChanged(); ElMessage.error(errorMessage(error)) }
    finally { reprocessingId.value = null }
  }

  async function retryTask(task: DocumentProcessingTaskItem) {
    if (retryingTaskId.value !== null) return
    retryingTaskId.value = task.id
    try { upsertTask(await retryDocumentProcessingTask(knowledgeId, task.id)); ElMessage.success('任务已重新进入队列'); schedulePoll(300) }
    catch (error) { ElMessage.error(errorMessage(error)) }
    finally { retryingTaskId.value = null }
  }

  async function cancelTask(task: DocumentProcessingTaskItem) {
    if (cancellingTaskId.value !== null) return
    cancellingTaskId.value = task.id
    try {
      const cancelled = await cancelDocumentProcessingTask(knowledgeId, task.id)
      upsertTask(cancelled)
      ElMessage.success(cancelled.status === 'CANCELLED' ? '任务已取消' : '取消请求已提交')
      if (isTaskActive(cancelled)) schedulePoll(300)
      else { await load(); await notifyChanged() }
    } catch (error) { ElMessage.error(errorMessage(error)) }
    finally { cancellingTaskId.value = null }
  }

  async function remove(document: DocumentItem) {
    if (deletingId.value !== null) return
    try {
      await ElMessageBox.confirm(`确认删除“${document.name}”吗？删除后，原始文件和全部切片都会被清理。`, '删除文档', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
    } catch { return }
    deletingId.value = document.id
    try {
      await deleteDocument(knowledgeId, document.id)
      if (previewDocument.value?.id === document.id) {
        paragraphGuard.invalidate(); paragraphVisible.value = false; previewDocument.value = null; paragraphs.value = []; paragraphTotal.value = 0
      }
      await load(); await notifyChanged(); ElMessage.success('文档、切片和原始文件已删除')
    } catch (error) { ElMessage.error(errorMessage(error)) }
    finally { deletingId.value = null }
  }

  function dispose() {
    stopPolling(); listGuard.invalidate(); taskGuard.invalidate(); chunkGuard.invalidate(); paragraphGuard.invalidate()
  }

  return {
    documents, loading, uploading, deletingId, reprocessingId, processingTasks, taskLoading,
    retryingTaskId, cancellingTaskId, activeTasks, hasDocuments, paragraphVisible, previewDocument,
    paragraphs, paragraphLoading, paragraphPage, paragraphPageSize, paragraphTotal, chunkVisible,
    chunkDocument, chunkLoading, chunkPreviewLoading, chunkReindexing, chunkPreview, chunkForm,
    load, loadTasks, taskForDocument, isTaskActive, chooseFile, loadParagraphs, openParagraphs,
    openChunkSettings, runChunkPreview, saveChunkConfigAndReindex, reprocess, retryTask, cancelTask, remove, dispose,
  }
}
