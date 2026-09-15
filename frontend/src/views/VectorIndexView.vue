<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppShell from '../components/AppShell.vue'
import {
  cancelVectorMigration, createVectorMigration, getVectorIndexStatus, listKnowledgeBases,
  listVectorMigrations, retryVectorMigration, type KnowledgeBase, type VectorIndexStatus,
  type VectorMigrationItem,
} from '../api'
import { errorMessage } from '../api/client'

const loading = ref(false)
const acting = ref<number | 'create' | null>(null)
const status = ref<VectorIndexStatus | null>(null)
const migrations = ref<VectorMigrationItem[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const selectedKnowledgeBaseId = ref<number | null>(null)
const batchSize = ref(200)
const coveragePercent = computed(() => Math.round((status.value?.coverage_ratio || 0) * 10000) / 100)

async function load() {
  loading.value = true
  try {
    const [indexStatus, migrationPage, knowledgePage] = await Promise.all([
      getVectorIndexStatus(), listVectorMigrations(), listKnowledgeBases({ page_size: 100 }),
    ])
    status.value = indexStatus
    migrations.value = migrationPage.items
    knowledgeBases.value = knowledgePage.items
    if (!selectedKnowledgeBaseId.value && knowledgeBases.value.length) selectedKnowledgeBaseId.value = knowledgeBases.value[0].id
  } catch (error) { ElMessage.error(errorMessage(error)) } finally { loading.value = false }
}

async function createMigration() {
  if (!selectedKnowledgeBaseId.value) return ElMessage.warning('请先选择知识库')
  acting.value = 'create'
  try {
    await createVectorMigration(selectedKnowledgeBaseId.value, batchSize.value)
    ElMessage.success('向量迁移已进入任务队列')
    await load()
  } catch (error) { ElMessage.error(errorMessage(error)) } finally { acting.value = null }
}

async function retry(row: VectorMigrationItem) {
  acting.value = row.id
  try { await retryVectorMigration(row.id); await load() } catch (error) { ElMessage.error(errorMessage(error)) } finally { acting.value = null }
}
async function cancel(row: VectorMigrationItem) {
  try {
    await ElMessageBox.confirm('当前批次完成后停止，不会删除已迁移向量。确认取消？', '取消迁移', { type: 'warning' })
    acting.value = row.id
    await cancelVectorMigration(row.id)
    await load()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error)) } finally { acting.value = null }
}
onMounted(load)
</script>

<template>
  <AppShell eyebrow="VECTOR OPERATIONS" title="向量索引" description="管理 Legacy 到 pgvector 的无损迁移、覆盖率与灰度检索状态。">
    <template #actions><el-button :loading="loading" @click="load">刷新状态</el-button></template>
    <div v-loading="loading" class="vector-page">
      <el-alert v-if="status && !status.pgvector_available" type="warning" :closable="false" show-icon
        title="当前数据库不支持 pgvector，系统继续使用 Legacy 检索。请在 PostgreSQL + pgvector 环境执行迁移。" />
      <section class="metric-grid">
        <article><span>读取模式</span><strong>{{ status?.read_mode || '-' }}</strong><small>{{ status?.search_mode || '-' }}</small></article>
        <article><span>写入模式</span><strong>{{ status?.write_mode || '-' }}</strong><small>可随时回退</small></article>
        <article><span>向量覆盖率</span><strong>{{ coveragePercent }}%</strong><small>{{ status?.covered_paragraph_count || 0 }} / {{ status?.legacy_vector_count || 0 }}</small></article>
        <article><span>向量空间</span><strong>{{ status?.spaces.length || 0 }}</strong><small>{{ status?.pgvector_row_count || 0 }} 行</small></article>
      </section>
      <section class="panel create-panel">
        <div><h2>创建回填任务</h2><p>只复制已有 JSON 向量，不调用模型；重复执行不会产生重复行。</p></div>
        <el-select v-model="selectedKnowledgeBaseId" placeholder="选择知识库" style="width:220px">
          <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-input-number v-model="batchSize" :min="10" :max="2000" :step="50" />
        <el-button type="primary" :loading="acting==='create'" :disabled="!status?.pgvector_available" @click="createMigration">开始回填</el-button>
      </section>
      <section class="panel">
        <h2>迁移记录</h2>
        <el-table :data="migrations" empty-text="暂无迁移记录">
          <el-table-column prop="space.model_name" label="向量空间" min-width="170" />
          <el-table-column prop="space.dimension" label="维度" width="80" />
          <el-table-column prop="status" label="状态" width="130" />
          <el-table-column label="进度" min-width="190"><template #default="{row}"><el-progress :percentage="row.progress" :status="row.status==='FAILURE'?'exception':row.status==='SUCCESS'?'success':undefined" /></template></el-table-column>
          <el-table-column prop="failed_count" label="失败" width="75" />
          <el-table-column prop="error_message" label="安全错误摘要" min-width="190" show-overflow-tooltip />
          <el-table-column label="操作" width="130"><template #default="{row}">
            <el-button v-if="['FAILURE','CANCELLED'].includes(row.status)" link type="primary" :loading="acting===row.id" @click="retry(row)">重试</el-button>
            <el-button v-if="['PENDING','RUNNING'].includes(row.status)" link type="danger" :loading="acting===row.id" @click="cancel(row)">取消</el-button>
          </template></el-table-column>
        </el-table>
      </section>
    </div>
  </AppShell>
</template>

<style scoped>
.vector-page{display:grid;gap:18px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric-grid article,.panel{background:#fff;border:1px solid #e3ebf7;border-radius:16px;box-shadow:0 8px 28px rgba(30,64,175,.045)}.metric-grid article{padding:18px}.metric-grid span,.metric-grid small{display:block;color:#8996aa;font-size:12px}.metric-grid strong{display:block;margin:9px 0 7px;color:#173d8f;font-size:24px}.panel{padding:20px}.panel h2{margin:0 0 6px;color:#26344d;font-size:16px}.panel p{margin:0;color:#8290a3;font-size:12px}.create-panel{display:flex;align-items:center;gap:14px}.create-panel>div{margin-right:auto}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.create-panel{align-items:stretch;flex-direction:column}.create-panel>div{margin:0}}
</style>
