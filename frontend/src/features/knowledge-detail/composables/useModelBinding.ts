import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getKnowledgeBaseModelStatus,
  listModelConfigs,
  updateKnowledgeBaseModelStatus,
  type KnowledgeBaseModelStatus,
  type ModelConfigItem,
} from '../../../api'
import { errorMessage } from '../../../api/client'
import { useRequestGuard } from './useRequestGuard'

export function useModelBinding(knowledgeId: number) {
  const visible = ref(false)
  const status = ref<KnowledgeBaseModelStatus | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const chatConfigs = ref<ModelConfigItem[]>([])
  const embeddingConfigs = ref<ModelConfigItem[]>([])
  const selectedChatConfigId = ref<number | null>(null)
  const selectedEmbeddingConfigId = ref<number | null>(null)
  const guard = useRequestGuard()
  const summary = computed(() => status.value
    ? `Chat：${status.value.chat.label} · Embedding：${status.value.embedding.label}`
    : '正在读取模型状态')

  async function load(showError = true) {
    const sequence = guard.next()
    loading.value = true
    try {
      const result = await getKnowledgeBaseModelStatus(knowledgeId)
      if (guard.current(sequence)) status.value = result
    } catch (error) {
      if (guard.current(sequence) && showError) ElMessage.error(errorMessage(error))
    } finally {
      if (guard.current(sequence)) loading.value = false
    }
  }

  async function open() {
    visible.value = true
    const sequence = guard.next()
    loading.value = true
    try {
      const [modelStatus, chatPage, embeddingPage] = await Promise.all([
        getKnowledgeBaseModelStatus(knowledgeId), listModelConfigs('CHAT', 1, 100), listModelConfigs('EMBEDDING', 1, 100),
      ])
      if (!guard.current(sequence)) return
      status.value = modelStatus
      chatConfigs.value = chatPage.items
      embeddingConfigs.value = embeddingPage.items
      selectedChatConfigId.value = modelStatus.chat_model_config_id
      selectedEmbeddingConfigId.value = modelStatus.embedding_model_config_id
    } catch (error) {
      if (guard.current(sequence)) ElMessage.error(errorMessage(error))
    } finally {
      if (guard.current(sequence)) loading.value = false
    }
  }

  async function save(afterSave?: () => Promise<void>) {
    if (saving.value) return
    saving.value = true
    try {
      status.value = await updateKnowledgeBaseModelStatus(knowledgeId, {
        chat_model_config_id: selectedChatConfigId.value,
        embedding_model_config_id: selectedEmbeddingConfigId.value,
      })
      if (afterSave) await afterSave()
      visible.value = false
      ElMessage.success('知识库模型配置已更新')
    } catch (error) {
      ElMessage.error(errorMessage(error))
    } finally {
      saving.value = false
    }
  }

  function dispose() { guard.invalidate() }

  return {
    visible, status, loading, saving, chatConfigs, embeddingConfigs, selectedChatConfigId,
    selectedEmbeddingConfigId, summary, load, open, save, dispose,
  }
}
