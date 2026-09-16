<script setup lang="ts">
import type { DocumentChunkingConfig, DocumentChunkPreview, DocumentItem, ParagraphItem } from '../../../api'

defineProps<{
  previewDocument: DocumentItem | null
  paragraphs: ParagraphItem[]
  paragraphLoading: boolean
  paragraphPageSize: number
  paragraphTotal: number
  chunkDocument: DocumentItem | null
  chunkLoading: boolean
  chunkPreviewLoading: boolean
  chunkReindexing: boolean
  chunkPreview: DocumentChunkPreview | null
  chunkForm: DocumentChunkingConfig
}>()
const paragraphVisible = defineModel<boolean>('paragraphVisible', { required: true })
const paragraphPage = defineModel<number>('paragraphPage', { required: true })
const chunkVisible = defineModel<boolean>('chunkVisible', { required: true })
const emit = defineEmits<{ paragraphPage: [page: number]; preview: []; reindex: [] }>()
</script>

<template>
  <el-drawer v-model="chunkVisible" size="min(860px, 96vw)" destroy-on-close>
    <template #header><div class="paragraph-drawer-title"><strong>结构化解析与父子切片</strong><span>{{ chunkDocument?.name }}</span></div></template>
    <div v-loading="chunkLoading" class="chunk-settings-layout">
      <section class="chunk-settings-form">
        <el-form label-position="top">
          <el-form-item label="解析器"><el-select v-model="chunkForm.parser_type" class="full-button"><el-option label="自动识别" value="AUTO" /><el-option label="TXT" value="TXT" /><el-option label="Markdown" value="MARKDOWN" /><el-option label="PDF" value="PDF" /><el-option label="DOCX" value="DOCX" /></el-select></el-form-item>
          <el-form-item label="切片策略"><el-select v-model="chunkForm.chunk_strategy" class="full-button"><el-option label="父子切片（Child检索 / Parent生成）" value="PARENT_CHILD" /><el-option label="兼容切片" value="LEGACY" /></el-select></el-form-item>
          <el-form-item label="Parent 最大 Token 数"><el-input-number v-model="chunkForm.parent_max_tokens" :min="400" :max="4000" :step="100" /></el-form-item>
          <el-form-item label="Child 目标 Token 数"><el-input-number v-model="chunkForm.child_target_tokens" :min="100" :max="1200" :step="50" /></el-form-item>
          <el-form-item label="Child 重叠 Token 数"><el-input-number v-model="chunkForm.child_overlap_tokens" :min="0" :max="300" :step="10" /></el-form-item>
          <el-form-item label="结构保持"><el-checkbox v-model="chunkForm.preserve_tables">尽量保持表格整体</el-checkbox><el-checkbox v-model="chunkForm.preserve_code_blocks">尽量保持代码块整体</el-checkbox></el-form-item>
        </el-form>
        <el-alert type="info" :closable="false" title="修改表单不会改变正式切片；预览不生成Embedding，也不创建后台任务。" />
        <div class="chunk-settings-actions"><el-button :loading="chunkPreviewLoading" :disabled="chunkReindexing" @click="emit('preview')">预览切片</el-button><el-button type="primary" :loading="chunkReindexing" :disabled="chunkPreviewLoading" @click="emit('reindex')">保存并重新索引</el-button></div>
      </section>
      <section v-loading="chunkPreviewLoading" class="chunk-preview-tree">
        <el-empty v-if="!chunkPreviewLoading && !chunkPreview" description="点击“预览切片”查看真实文件的解析结果" />
        <template v-if="chunkPreview">
          <div class="chunk-preview-summary"><el-tag>{{ chunkPreview.parser_type }}</el-tag><span>{{ chunkPreview.block_count }} Blocks</span><span>{{ chunkPreview.parent_count }} Parent</span><span>{{ chunkPreview.child_count }} Child</span></div>
          <el-alert v-for="warning in chunkPreview.warnings" :key="warning" type="warning" :closable="false" :title="warning" />
          <el-alert v-if="chunkPreview.truncated" type="info" :closable="false" title="预览数量已截断，正式重新索引仍会处理全部内容。" />
          <article v-for="parent in chunkPreview.items" :key="parent.position ?? 'legacy'" class="chunk-parent-card">
            <header><strong>{{ parent.position === null ? 'Legacy切片组' : `Parent #${parent.position}` }}</strong><span>{{ parent.heading_path.join(' > ') || '无标题路径' }}</span><el-tag size="small">{{ parent.structure_type }}</el-tag><span>{{ parent.token_count }} tokens</span><span v-if="parent.page_start">第 {{ parent.page_start }}<template v-if="parent.page_end !== parent.page_start">–{{ parent.page_end }}</template> 页</span></header>
            <p v-if="parent.content" class="chunk-parent-content">{{ parent.content }}</p>
            <div class="chunk-child-list"><article v-for="child in parent.children" :key="child.position" class="chunk-child-card"><div><strong>Child #{{ child.position }}</strong><span>{{ child.heading_path.join(' > ') || '无标题路径' }}</span><el-tag size="small" effect="plain">{{ child.structure_type }}</el-tag><span>{{ child.token_count }} tokens</span></div><p>{{ child.content }}</p></article></div>
          </article>
        </template>
      </section>
    </div>
  </el-drawer>

  <el-drawer v-model="paragraphVisible" size="min(680px, 92vw)" destroy-on-close data-testid="paragraph-drawer">
    <template #header><div class="paragraph-drawer-title"><strong>文档切片</strong><span>{{ previewDocument?.name }}</span></div></template>
    <div v-loading="paragraphLoading" class="paragraph-preview"><el-empty v-if="!paragraphLoading && !paragraphs.length" description="暂无可预览的切片" /><article v-for="paragraph in paragraphs" :key="paragraph.id" class="paragraph-card"><div class="paragraph-number">切片 #{{ paragraph.position }} · {{ paragraph.chunk_type }} · {{ paragraph.token_count }} tokens<span v-if="paragraph.parent_position"> · Parent #{{ paragraph.parent_position }}</span></div><div class="paragraph-structure-meta"><span>{{ paragraph.heading_path.join(' > ') || '无标题路径' }}</span><span>{{ paragraph.structure_type }}</span><span v-if="paragraph.page_start">第 {{ paragraph.page_start }}<template v-if="paragraph.page_end !== paragraph.page_start">–{{ paragraph.page_end }}</template> 页</span></div><p>{{ paragraph.content }}</p></article></div>
    <template #footer><div class="paragraph-pagination"><span>共 {{ paragraphTotal }} 个切片</span><el-pagination v-model:current-page="paragraphPage" :page-size="paragraphPageSize" :total="paragraphTotal" layout="prev, pager, next" :disabled="paragraphLoading" @current-change="emit('paragraphPage', $event)" /></div></template>
  </el-drawer>
</template>
