import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  compareRetrieval,
  debugRetrieval,
  getRetrievalCapabilities,
  getRetrievalConfig,
  updateRetrievalConfig,
  type RetrievalCapabilities,
  type RetrievalCompareResult,
  type RetrievalConfig,
  type RetrievalDebugResult,
} from '../../../api'
import { errorMessage } from '../../../api/client'
import { useRequestGuard } from './useRequestGuard'

const defaults: RetrievalConfig = {
  retrieval_mode: 'VECTOR', retrieval_top_k: 5, similarity_threshold: 0, vector_weight: 1,
  fusion_method: 'WEIGHTED', vector_candidate_k: 30, keyword_candidate_k: 30, rrf_k: 60,
  rerank_enabled: false, rerank_candidate_k: 20, max_context_chars: 6000,
  system_prompt: '', no_answer_message: '',
}

export function useRetrievalWorkbench(knowledgeId: number) {
  const settingsVisible = ref(false)
  const debugVisible = ref(false)
  const config = ref<RetrievalConfig | null>(null)
  const configLoading = ref(false)
  const configSaving = ref(false)
  const form = reactive<RetrievalConfig>({ ...defaults })
  const query = ref('')
  const debugLoading = ref(false)
  const debugResult = ref<RetrievalDebugResult | null>(null)
  const capabilities = ref<RetrievalCapabilities | null>(null)
  const capabilitiesLoading = ref(false)
  const compareLoading = ref(false)
  const compareResult = ref<RetrievalCompareResult | null>(null)
  const configGuard = useRequestGuard()
  const debugGuard = useRequestGuard()
  const compareGuard = useRequestGuard()
  const keywordWeight = computed(() => Number((1 - form.vector_weight).toFixed(2)))

  function copy(value: RetrievalConfig) { Object.assign(form, value) }
  function resetDefaults() { copy(defaults) }

  async function loadConfig(showError = true) {
    const sequence = configGuard.next()
    configLoading.value = true
    try {
      const value = await getRetrievalConfig(knowledgeId)
      if (!configGuard.current(sequence)) return
      config.value = value
      copy(value)
    } catch (error) {
      if (configGuard.current(sequence) && showError) ElMessage.error(errorMessage(error))
    } finally {
      if (configGuard.current(sequence)) configLoading.value = false
    }
  }

  async function loadCapabilities(showError = true) {
    capabilitiesLoading.value = true
    try { capabilities.value = await getRetrievalCapabilities() }
    catch (error) {
      capabilities.value = null
      if (showError) ElMessage.error(errorMessage(error))
    } finally { capabilitiesLoading.value = false }
  }

  async function openSettings() {
    settingsVisible.value = true
    await Promise.all([loadConfig(), loadCapabilities(false)])
  }

  async function saveSettings() {
    if (configSaving.value) return
    configSaving.value = true
    try {
      const value = await updateRetrievalConfig(knowledgeId, {
        ...form,
        rerank_enabled: form.retrieval_mode === 'HYBRID' && form.fusion_method === 'RRF' && form.rerank_enabled,
      })
      config.value = value
      copy(value)
      settingsVisible.value = false
      debugResult.value = null
      compareResult.value = null
      ElMessage.success('检索配置已更新，后续问答立即使用新策略')
    } catch (error) { ElMessage.error(errorMessage(error)) }
    finally { configSaving.value = false }
  }

  async function openDebug() {
    debugVisible.value = true
    debugResult.value = null
    compareResult.value = null
    await Promise.all([
      config.value ? Promise.resolve() : loadConfig(false),
      capabilities.value ? Promise.resolve() : loadCapabilities(false),
    ])
  }

  async function runDebug() {
    const question = query.value.trim()
    if (!question || debugLoading.value) {
      if (!question) ElMessage.warning('请输入测试问题')
      return
    }
    const sequence = debugGuard.next()
    debugLoading.value = true
    try {
      const value = await debugRetrieval(knowledgeId, question, 20)
      if (debugGuard.current(sequence)) debugResult.value = value
    } catch (error) {
      if (debugGuard.current(sequence)) {
        debugResult.value = null
        ElMessage.error(errorMessage(error))
      }
    } finally { if (debugGuard.current(sequence)) debugLoading.value = false }
  }

  async function runCompare() {
    const question = query.value.trim()
    if (!question || compareLoading.value) {
      if (!question) ElMessage.warning('请输入测试问题')
      return
    }
    const sequence = compareGuard.next()
    compareLoading.value = true
    try {
      const value = await compareRetrieval(knowledgeId, question, {
        retrieval_mode: 'HYBRID', fusion_method: 'RRF',
        vector_candidate_k: form.vector_candidate_k,
        keyword_candidate_k: form.keyword_candidate_k,
        rrf_k: form.rrf_k,
        rerank_enabled: Boolean(capabilities.value?.reranker_ready),
        rerank_candidate_k: form.rerank_candidate_k,
      }, 20)
      if (compareGuard.current(sequence)) compareResult.value = value
    } catch (error) {
      if (compareGuard.current(sequence)) {
        compareResult.value = null
        ElMessage.error(errorMessage(error))
      }
    } finally { if (compareGuard.current(sequence)) compareLoading.value = false }
  }

  function scoreText(value: number | null) { return value === null ? '—' : value.toFixed(4) }
  function dispose() { configGuard.invalidate(); debugGuard.invalidate(); compareGuard.invalidate() }

  return {
    settingsVisible, debugVisible, config, configLoading, configSaving, form, keywordWeight,
    query, debugLoading, debugResult, capabilities, capabilitiesLoading, compareLoading, compareResult,
    resetDefaults, loadConfig, openSettings, saveSettings, openDebug, runDebug, runCompare, scoreText, dispose,
  }
}
