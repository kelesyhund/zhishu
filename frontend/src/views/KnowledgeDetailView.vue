<script setup lang="ts">
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChatWorkbench from '../features/knowledge-detail/components/ChatWorkbench.vue'
import DocumentPanel from '../features/knowledge-detail/components/DocumentPanel.vue'
import { useAgentSettings } from '../features/knowledge-detail/composables/useAgentSettings'
import { useConversationChat } from '../features/knowledge-detail/composables/useConversationChat'
import { useDocumentManagement } from '../features/knowledge-detail/composables/useDocumentManagement'
import { useKnowledgeContext } from '../features/knowledge-detail/composables/useKnowledgeContext'
import { useModelBinding } from '../features/knowledge-detail/composables/useModelBinding'
import { useRetrievalWorkbench } from '../features/knowledge-detail/composables/useRetrievalWorkbench'

const AgentSettingsDrawer = defineAsyncComponent(() => import('../features/knowledge-detail/components/AgentSettingsDrawer.vue'))
const ConversationDrawer = defineAsyncComponent(() => import('../features/knowledge-detail/components/ConversationDrawer.vue'))
const DocumentDrawers = defineAsyncComponent(() => import('../features/knowledge-detail/components/DocumentDrawers.vue'))
const ModelBindingDrawer = defineAsyncComponent(() => import('../features/knowledge-detail/components/ModelBindingDrawer.vue'))
const RetrievalDrawers = defineAsyncComponent(() => import('../features/knowledge-detail/components/RetrievalDrawers.vue'))

const route = useRoute()
const router = useRouter()
const knowledgeId = Number(route.params.id)
const knowledge = useKnowledgeContext(knowledgeId)
const model = useModelBinding(knowledgeId)
const agent = useAgentSettings(knowledgeId)
const documentsModule = useDocumentManagement(knowledgeId, { onDocumentsChanged: () => model.load(false) })
const retrieval = useRetrievalWorkbench(knowledgeId)
const conversation = useConversationChat(knowledgeId, {
  route,
  router,
  hasDocuments: documentsModule.hasDocuments,
  agentEnabled: agent.enabled,
  agentConfig: agent.config,
  ensureTrace: agent.ensureTrace,
  updateTool: agent.updateTool,
})
const chatWorkbench = ref<InstanceType<typeof ChatWorkbench>>()

async function saveModelBinding() {
  await model.save(async () => { await Promise.all([documentsModule.load(), agent.load(false)]) })
}

onMounted(async () => {
  conversation.chatBox.value = chatWorkbench.value?.getScrollElement()
  await Promise.all([
    knowledge.load(), documentsModule.load(), documentsModule.loadTasks(), conversation.loadConversations(1),
    model.load(false), retrieval.loadConfig(false), agent.load(false),
  ])
  await conversation.restoreFromUrl()
})

onBeforeUnmount(() => {
  knowledge.dispose(); documentsModule.dispose(); conversation.dispose(); model.dispose(); retrieval.dispose(); agent.dispose()
})
</script>

<template>
  <div v-if="!Number.isInteger(knowledgeId) || knowledgeId <= 0 || knowledge.notFound.value" class="page-state">
    <el-result icon="warning" title="知识库不存在或无权访问"><template #extra><el-button type="primary" @click="router.push('/knowledge')">返回知识空间</el-button></template></el-result>
  </div>
  <div v-else v-loading="knowledge.loading.value" class="detail-page">
    <DocumentPanel
      :knowledge-name="knowledge.knowledgeBase.value?.name || ''"
      :documents="documentsModule.documents.value"
      :tasks="documentsModule.processingTasks.value"
      :uploading="documentsModule.uploading.value"
      :deleting-id="documentsModule.deletingId.value"
      :reprocessing-id="documentsModule.reprocessingId.value"
      :retrying-task-id="documentsModule.retryingTaskId.value"
      :cancelling-task-id="documentsModule.cancellingTaskId.value"
      @back="router.push('/knowledge')"
      @upload="documentsModule.chooseFile"
      @paragraphs="documentsModule.openParagraphs"
      @chunks="documentsModule.openChunkSettings"
      @retry="documentsModule.retryTask"
      @cancel="documentsModule.cancelTask"
      @reprocess="documentsModule.reprocess"
      @remove="documentsModule.remove"
    />

    <ChatWorkbench
      ref="chatWorkbench"
      v-model:input="conversation.input.value"
      :knowledge-name="knowledge.knowledgeBase.value?.name || ''"
      :description="knowledge.knowledgeBase.value?.description || ''"
      :active-title="conversation.activeTitle.value"
      :messages="conversation.messages.value"
      :history-loading="conversation.historyLoading.value"
      :history-has-previous="conversation.historyHasPrevious.value"
      :sending="conversation.sending.value"
      :generation-state="conversation.generationState.value"
      :agent-enabled="agent.enabled.value"
      :agent-loading="agent.loading.value"
      :model-status="model.status.value"
      :model-loading="model.loading.value"
      :model-summary="model.summary.value"
      @load-older="conversation.loadOlderMessages"
      @open-agent="agent.open"
      @open-model="model.open"
      @open-retrieval="retrieval.openSettings"
      @open-debug="retrieval.openDebug"
      @open-history="conversation.openHistory"
      @send="conversation.send"
      @stop="conversation.stopGenerating"
    />

    <AgentSettingsDrawer v-model:visible="agent.visible.value" :config="agent.config.value" :loading="agent.loading.value" :saving="agent.saving.value" :form="agent.form" @reset="agent.resetDefaults" @save="agent.save" />
    <ModelBindingDrawer v-model:visible="model.visible.value" v-model:chat-id="model.selectedChatConfigId.value" v-model:embedding-id="model.selectedEmbeddingConfigId.value" :status="model.status.value" :loading="model.loading.value" :saving="model.saving.value" :chat-configs="model.chatConfigs.value" :embedding-configs="model.embeddingConfigs.value" @save="saveModelBinding" @manage="router.push('/model-configs')" />
    <ConversationDrawer v-model:visible="conversation.drawerVisible.value" v-model:page="conversation.conversationPage.value" :conversations="conversation.conversations.value" :loading="conversation.conversationLoading.value" :page-size="conversation.conversationPageSize.value" :total="conversation.conversationTotal.value" :active-id="conversation.activeConversationId.value" :renaming-id="conversation.renamingId.value" :deleting-id="conversation.deletingId.value" :sending="conversation.sending.value" @create="conversation.startNewConversation" @select="conversation.selectConversation" @rename="conversation.renameConversation" @remove="conversation.removeConversation" @page-change="conversation.loadConversations" />
    <DocumentDrawers v-model:paragraph-visible="documentsModule.paragraphVisible.value" v-model:paragraph-page="documentsModule.paragraphPage.value" v-model:chunk-visible="documentsModule.chunkVisible.value" :preview-document="documentsModule.previewDocument.value" :paragraphs="documentsModule.paragraphs.value" :paragraph-loading="documentsModule.paragraphLoading.value" :paragraph-page-size="documentsModule.paragraphPageSize.value" :paragraph-total="documentsModule.paragraphTotal.value" :chunk-document="documentsModule.chunkDocument.value" :chunk-loading="documentsModule.chunkLoading.value" :chunk-preview-loading="documentsModule.chunkPreviewLoading.value" :chunk-reindexing="documentsModule.chunkReindexing.value" :chunk-preview="documentsModule.chunkPreview.value" :chunk-form="documentsModule.chunkForm" @paragraph-page="documentsModule.loadParagraphs" @preview="documentsModule.runChunkPreview" @reindex="documentsModule.saveChunkConfigAndReindex" />
    <RetrievalDrawers v-model:settings-visible="retrieval.settingsVisible.value" v-model:debug-visible="retrieval.debugVisible.value" v-model:query="retrieval.query.value" :form="retrieval.form" :keyword-weight="retrieval.keywordWeight.value" :config-loading="retrieval.configLoading.value" :config-saving="retrieval.configSaving.value" :capabilities="retrieval.capabilities.value" :capabilities-loading="retrieval.capabilitiesLoading.value" :debug-loading="retrieval.debugLoading.value" :debug-result="retrieval.debugResult.value" :compare-loading="retrieval.compareLoading.value" :compare-result="retrieval.compareResult.value" :score-text="retrieval.scoreText" @reset="retrieval.resetDefaults" @save="retrieval.saveSettings" @debug="retrieval.runDebug" @compare="retrieval.runCompare" />
  </div>
</template>
