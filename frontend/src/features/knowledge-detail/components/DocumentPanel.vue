<script setup lang="ts">
import { computed } from 'vue'
import { Delete, Document, Refresh, Setting, UploadFilled, View as ViewIcon } from '@element-plus/icons-vue'

import type { DocumentItem, DocumentProcessingTaskItem } from '../../../api'
import BrandLogo from '../../../components/BrandLogo.vue'
import { documentStatusTag, documentStatusText, taskStageText, taskStatusTag, taskStatusText } from '../status'

const props = defineProps<{
  knowledgeName: string
  documents: DocumentItem[]
  tasks: DocumentProcessingTaskItem[]
  uploading: boolean
  deletingId: number | null
  reprocessingId: number | null
  retryingTaskId: number | null
  cancellingTaskId: number | null
}>()
const emit = defineEmits<{
  back: []
  upload: [event: Event]
  paragraphs: [document: DocumentItem]
  chunks: [document: DocumentItem]
  retry: [task: DocumentProcessingTaskItem]
  cancel: [task: DocumentProcessingTaskItem]
  reprocess: [document: DocumentItem]
  remove: [document: DocumentItem]
}>()
const taskByDocument = computed(() => new Map(props.tasks.map((task) => [task.document_id, task])))
const active = new Set(['PENDING', 'PROCESSING', 'RETRYING', 'CANCEL_REQUESTED'])
</script>

<template>
  <aside class="document-panel" data-testid="document-panel">
    <div class="detail-brand"><BrandLogo /><span><i />知识空间在线</span></div>
    <button class="back-button" data-testid="knowledge-back" @click="emit('back')">返回知识空间</button>
    <div class="panel-title"><span>KNOWLEDGE BASE</span><div><h2>{{ knowledgeName || '文档' }}</h2><p>支持 TXT、MD、PDF、DOCX，最大10MB</p></div></div>
    <label class="upload-button" :class="{ disabled: uploading }" data-testid="document-upload">
      <input type="file" accept=".txt,.md,.pdf,.docx" :disabled="uploading" @change="emit('upload', $event)" />
      <el-icon><UploadFilled /></el-icon>{{ uploading ? '正在上传...' : '上传文档' }}
    </label>
    <div class="document-list" v-loading="false">
      <div v-for="item in documents" :key="item.id" class="document-row" :data-testid="`document-${item.id}`">
        <div class="document-summary">
          <el-icon class="document-icon"><Document /></el-icon>
          <div class="document-info">
            <strong :title="item.name">{{ item.name }}</strong>
            <div class="document-meta">
              <el-tag size="small" effect="light" :type="documentStatusTag(item.status)">{{ documentStatusText(item.status) }}</el-tag>
              <span>{{ item.parent_chunk_count }} Parent / {{ item.paragraph_count }} Child</span>
            </div>
            <el-tag v-if="item.needs_reprocess" class="document-stale-tag" size="small" type="warning">向量需更新</el-tag>
          </div>
        </div>
        <p v-if="item.error_message" class="document-error">{{ item.error_message }}</p>
        <div v-if="taskByDocument.get(item.id)" class="document-task">
          <div class="document-task-head">
            <el-tag size="small" :type="taskStatusTag(taskByDocument.get(item.id)!.status)">{{ taskStatusText(taskByDocument.get(item.id)!.status) }}</el-tag>
            <span>{{ taskStageText(taskByDocument.get(item.id)!.current_stage) }}</span>
            <span>第 {{ taskByDocument.get(item.id)!.attempt_count }} 次尝试</span>
          </div>
          <el-progress :percentage="taskByDocument.get(item.id)!.progress" :status="taskByDocument.get(item.id)!.status === 'SUCCESS' ? 'success' : undefined" :stroke-width="6" />
          <p v-if="taskByDocument.get(item.id)!.error_message" class="document-error">{{ taskByDocument.get(item.id)!.error_message }}</p>
        </div>
        <div class="document-actions">
          <el-button text size="small" :icon="ViewIcon" @click="emit('paragraphs', item)">查看切片</el-button>
          <el-button text size="small" :icon="Setting" @click="emit('chunks', item)">切片设置</el-button>
          <el-button v-if="['FAILURE', 'ENQUEUE_FAILED', 'CANCELLED'].includes(taskByDocument.get(item.id)?.status || '')" text size="small" type="warning" :icon="Refresh" :loading="retryingTaskId === taskByDocument.get(item.id)?.id" :disabled="retryingTaskId !== null || cancellingTaskId !== null" @click="emit('retry', taskByDocument.get(item.id)!)">重试任务</el-button>
          <el-button v-if="active.has(taskByDocument.get(item.id)?.status || '')" text size="small" type="warning" :loading="cancellingTaskId === taskByDocument.get(item.id)?.id" :disabled="cancellingTaskId !== null" @click="emit('cancel', taskByDocument.get(item.id)!)">取消处理</el-button>
          <el-button text size="small" :icon="Refresh" :loading="reprocessingId === item.id" :disabled="reprocessingId !== null || deletingId !== null || active.has(taskByDocument.get(item.id)?.status || '')" @click="emit('reprocess', item)">重新处理</el-button>
          <el-button text size="small" type="danger" :icon="Delete" :loading="deletingId === item.id" :disabled="deletingId !== null || reprocessingId !== null" @click="emit('remove', item)">删除</el-button>
        </div>
      </div>
      <p v-if="!documents.length" class="empty-text" data-testid="document-empty">还没有文档</p>
    </div>
  </aside>
</template>
