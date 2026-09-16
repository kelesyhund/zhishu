<script setup lang="ts">
import type { KnowledgeBaseModelStatus, ModelConfigItem } from '../../../api'

defineProps<{ status: KnowledgeBaseModelStatus | null; loading: boolean; saving: boolean; chatConfigs: ModelConfigItem[]; embeddingConfigs: ModelConfigItem[] }>()
const visible = defineModel<boolean>('visible', { required: true })
const chatId = defineModel<number | null>('chatId', { required: true })
const embeddingId = defineModel<number | null>('embeddingId', { required: true })
const emit = defineEmits<{ save: []; manage: [] }>()
</script>

<template>
  <el-drawer v-model="visible" size="min(520px, 92vw)">
    <template #header><div class="model-settings-title"><strong>知识库模型设置</strong><span>分别选择问答模型和文档向量模型</span></div></template>
    <div v-loading="loading" class="model-settings-form">
      <el-alert v-if="status?.embedding_stale_document_count" type="warning" :closable="false" :title="`${status.embedding_stale_document_count} 个文档的向量与当前 Embedding 配置不一致，请保存后重新处理文档。`" />
      <el-form label-position="top">
        <el-form-item label="Chat 模型"><el-select v-model="chatId" class="full-button" placeholder="系统默认"><el-option label="系统默认（环境配置或检索降级）" :value="null" /><el-option v-for="item in chatConfigs" :key="item.id" :label="`${item.name} / ${item.model_name}`" :value="item.id" /></el-select></el-form-item>
        <el-form-item label="Embedding 模型"><el-select v-model="embeddingId" class="full-button" placeholder="系统默认"><el-option label="系统默认（环境配置或内置向量）" :value="null" /><el-option v-for="item in embeddingConfigs" :key="item.id" :label="`${item.name} / ${item.model_name}`" :value="item.id" /></el-select></el-form-item>
      </el-form>
      <div v-if="status" class="effective-model-status"><p><strong>当前 Chat：</strong>{{ status.chat.label }}<span v-if="status.chat.model_name"> / {{ status.chat.model_name }}</span></p><p><strong>当前 Embedding：</strong>{{ status.embedding.label }}<span v-if="status.embedding.model_name"> / {{ status.embedding.model_name }}</span></p></div>
      <el-button text type="primary" @click="emit('manage')">管理 OpenAI 兼容模型配置</el-button>
    </div>
    <template #footer><el-button :disabled="saving" @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="emit('save')">保存选择</el-button></template>
  </el-drawer>
</template>
