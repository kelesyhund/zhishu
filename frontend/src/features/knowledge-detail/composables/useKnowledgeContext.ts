import { ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getKnowledgeBase, type KnowledgeBase } from '../../../api'
import { errorMessage, errorStatus } from '../../../api/client'
import { useRequestGuard } from './useRequestGuard'

export function useKnowledgeContext(knowledgeId: number) {
  const knowledgeBase = ref<KnowledgeBase | null>(null)
  const loading = ref(false)
  const notFound = ref(false)
  const guard = useRequestGuard()

  async function load() {
    const sequence = guard.next()
    loading.value = true
    notFound.value = false
    try {
      const value = await getKnowledgeBase(knowledgeId)
      if (guard.current(sequence)) knowledgeBase.value = value
    } catch (error) {
      if (!guard.current(sequence)) return
      notFound.value = errorStatus(error) === 404
      ElMessage.error(errorMessage(error))
    } finally { if (guard.current(sequence)) loading.value = false }
  }

  function dispose() { guard.invalidate() }
  return { knowledgeBase, loading, notFound, load, dispose }
}
