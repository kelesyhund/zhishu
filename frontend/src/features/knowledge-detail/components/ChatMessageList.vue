<script setup lang="ts">
import type { ChatMessage } from '../types'
import { renderSafeMarkdown } from '../../../utils/markdown'
import { agentStatusTag, agentStatusText, toolStatusTag, toolStatusText } from '../status'

defineProps<{ messages: ChatMessage[] }>()
const safeJson = (value: Record<string, unknown>) => JSON.stringify(value, null, 2)
</script>

<template>
  <div v-if="!messages.length" class="chat-welcome" data-testid="chat-empty">
    <div class="welcome-symbol">枢</div><span>TRUSTED ANSWERS</span><h2>从企业知识中寻找可靠答案</h2><p>上传文档或选择历史会话，每条回答都会尽量附带可追溯的引用。</p>
  </div>
  <article v-for="(message, index) in messages" :key="message.id || index" class="message" :class="message.role" :data-testid="`chat-message-${message.role}`">
    <div class="message-avatar">{{ message.role === 'user' ? '你' : 'AI' }}</div>
    <div class="message-body">
      <div v-if="message.role === 'assistant'" class="markdown-body" v-html="renderSafeMarkdown(message.content || '正在思考…')" />
      <div v-else>{{ message.content }}</div>
      <details v-if="message.agentTrace" class="agent-trace" :open="message.agentTrace.status === 'RUNNING'">
        <summary><span>Agent 执行轨迹</span><el-tag size="small" :type="agentStatusTag(message.agentTrace.status)">{{ agentStatusText(message.agentTrace.status) }}</el-tag><small>{{ message.agentTrace.step_count }} 个步骤</small></summary>
        <p v-if="message.agentTrace.error_message" class="agent-trace-error">{{ message.agentTrace.error_message }}</p>
        <el-empty v-if="!message.agentTrace.tool_executions.length" :image-size="48" description="本次回答未调用工具" />
        <article v-for="execution in message.agentTrace.tool_executions" :key="execution.id" class="tool-execution">
          <header><strong>步骤 {{ execution.step }}.{{ execution.sequence }} · {{ execution.tool_name }}</strong><span><el-tag size="small" :type="toolStatusTag(execution.status)">{{ toolStatusText(execution.status) }}</el-tag>{{ execution.latency_ms }} ms</span></header>
          <div class="tool-execution-block"><span>安全参数</span><pre>{{ safeJson(execution.arguments) }}</pre></div>
          <p v-if="execution.result_summary" class="tool-result-summary">{{ execution.result_summary }}</p>
          <p v-if="execution.error_message" class="agent-trace-error">{{ execution.error_message }}</p>
        </article>
      </details>
      <div v-if="message.references?.length" class="references" data-testid="chat-references">
        <strong>引用资料</strong>
        <details v-for="(reference, refIndex) in message.references" :key="refIndex"><summary>[{{ refIndex + 1 }}] {{ reference.document_name }} · 相似度 {{ reference.similarity }}</summary><p>{{ reference.content }}</p></details>
      </div>
    </div>
  </article>
</template>
