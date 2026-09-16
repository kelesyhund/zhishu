<script setup lang="ts">
import { ref } from 'vue'
import { ChatLineRound, Setting } from '@element-plus/icons-vue'

import type { KnowledgeBaseModelStatus } from '../../../api'
import type { ChatMessage } from '../types'
import type { GenerationState } from '../composables/useConversationChat'
import ChatMessageList from './ChatMessageList.vue'

defineProps<{
  knowledgeName: string
  description: string
  activeTitle: string
  messages: ChatMessage[]
  historyLoading: boolean
  historyHasPrevious: boolean
  sending: boolean
  generationState: GenerationState
  agentEnabled: boolean
  agentLoading: boolean
  modelStatus: KnowledgeBaseModelStatus | null
  modelLoading: boolean
  modelSummary: string
}>()
const emit = defineEmits<{
  loadOlder: []
  openAgent: []
  openModel: []
  openRetrieval: []
  openDebug: []
  openHistory: []
  send: []
  stop: []
}>()
const input = defineModel<string>('input', { required: true })
const scrollElement = ref<HTMLElement>()
defineExpose({ getScrollElement: () => scrollElement.value })
</script>

<template>
  <main class="chat-panel" data-testid="chat-workbench">
    <header class="chat-header">
      <div><span class="chat-context-label">KNOWLEDGE ASSISTANT</span><h1>{{ knowledgeName || '知识库问答' }}</h1><p>{{ activeTitle }} · {{ description || '答案将尽量依据左侧上传的资料生成' }}</p></div>
      <div class="chat-header-actions">
        <el-button :loading="agentLoading" @click="emit('openAgent')">Agent 设置</el-button>
        <el-button :icon="Setting" :loading="modelLoading" @click="emit('openModel')">模型设置</el-button>
        <el-button @click="emit('openRetrieval')">检索设置</el-button>
        <el-button @click="emit('openDebug')">检索调试</el-button>
        <el-button :icon="ChatLineRound" @click="emit('openHistory')">历史会话</el-button>
        <el-tag :type="agentEnabled ? 'warning' : 'info'">{{ agentEnabled ? 'Agent 模式' : '普通 RAG' }}</el-tag>
        <el-tag :type="modelStatus?.chat.source === 'LOCAL' ? 'info' : 'success'">{{ modelStatus?.chat.label || '模型状态' }}</el-tag>
      </div>
    </header>
    <section ref="scrollElement" v-loading="historyLoading" class="chat-messages">
      <div v-if="historyHasPrevious" class="older-message-wrap"><el-button size="small" :loading="historyLoading" @click="emit('loadOlder')">加载更早消息</el-button></div>
      <ChatMessageList :messages="messages" />
    </section>
    <footer class="chat-input-wrap">
      <div class="chat-input">
        <el-input v-model="input" data-testid="chat-input" type="textarea" :rows="2" resize="none" placeholder="向知枢提问，Ctrl + Enter 发送" @keydown.ctrl.enter.prevent="emit('send')" />
        <el-button v-if="sending" type="warning" plain data-testid="chat-stop" @click="emit('stop')">停止生成</el-button>
        <el-button v-else type="primary" data-testid="chat-send" @click="emit('send')">发送问题</el-button>
      </div>
      <p>{{ agentEnabled ? 'Agent 模式已开启' : '普通 RAG 模式' }} · {{ modelSummary }}<span v-if="modelStatus?.embedding_stale_document_count"> · {{ modelStatus.embedding_stale_document_count }} 个文档需要重新处理</span><span v-if="generationState === 'CANCELLED'"> · 上次生成已取消</span></p>
    </footer>
  </main>
</template>
