<script setup lang="ts">
import { Delete, EditPen, Plus } from '@element-plus/icons-vue'
import type { ConversationItem } from '../../../api'

defineProps<{
  conversations: ConversationItem[]
  loading: boolean
  pageSize: number
  total: number
  activeId: number | null
  renamingId: number | null
  deletingId: number | null
  sending: boolean
}>()
const visible = defineModel<boolean>('visible', { required: true })
const page = defineModel<number>('page', { required: true })
const emit = defineEmits<{ create: []; select: [id: number]; rename: [item: ConversationItem]; remove: [item: ConversationItem]; pageChange: [page: number] }>()
</script>

<template>
  <el-drawer v-model="visible" size="min(440px, 92vw)" data-testid="conversation-drawer">
    <template #header><div class="conversation-drawer-title"><strong>历史会话</strong><span>选择会话可以恢复之前的消息</span></div></template>
    <el-button class="new-conversation-button" type="primary" plain :icon="Plus" :disabled="sending" data-testid="new-conversation" @click="emit('create')">新建对话</el-button>
    <div v-loading="loading" class="conversation-list">
      <el-empty v-if="!loading && !conversations.length" description="还没有历史会话" />
      <div v-for="conversation in conversations" :key="conversation.id" class="conversation-row" :class="{ active: activeId === conversation.id }" :data-testid="`conversation-${conversation.id}`">
        <button class="conversation-select" @click="emit('select', conversation.id)"><span class="conversation-summary"><strong :title="conversation.title">{{ conversation.title }}</strong><span>{{ conversation.message_count }} 条消息 · {{ new Date(conversation.last_message_at || conversation.created_at).toLocaleString() }}</span></span></button>
        <div class="conversation-actions">
          <el-button text size="small" :icon="EditPen" :loading="renamingId === conversation.id" :disabled="sending || renamingId !== null || deletingId !== null" aria-label="修改会话标题" @click="emit('rename', conversation)" />
          <el-button text size="small" type="danger" :icon="Delete" :loading="deletingId === conversation.id" :disabled="sending || deletingId !== null || renamingId !== null" aria-label="删除会话" @click="emit('remove', conversation)" />
        </div>
      </div>
    </div>
    <div v-if="total" class="conversation-pagination"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev, pager, next" :disabled="loading" @current-change="emit('pageChange', $event)" /></div>
  </el-drawer>
</template>
