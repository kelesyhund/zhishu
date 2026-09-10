<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, Delete, Edit, Plus, Search, Setting } from '@element-plus/icons-vue'

import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listKnowledgeBases,
  updateKnowledgeBase,
  type KnowledgeBase,
} from '../api'
import { errorMessage } from '../api/client'
import AppShell from '../components/AppShell.vue'
import { useWorkspaceStore } from '../stores/workspace'

const items = ref<KnowledgeBase[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const editingId = ref<number | null>(null)
const saving = ref(false)
const keyword = ref('')
const page = ref(1)
const pageSize = ref(12)
const total = ref(0)
const form = reactive({ name: '', description: '' })
const router = useRouter()
const workspaceStore = useWorkspaceStore()
const canWrite = computed(() => workspaceStore.can('knowledge.write'))
const dialogTitle = computed(() => (dialogMode.value === 'create' ? '新建知识库' : '编辑知识库'))
const visibleDocumentCount = computed(() => items.value.reduce((sum, item) => sum + item.document_count, 0))
const readyKnowledgeCount = computed(() => items.value.filter((item) => item.document_count > 0).length)

async function load() {
  loading.value = true
  try {
    const result = await listKnowledgeBases({
      keyword: keyword.value.trim() || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = result.items
    total.value = result.total
    page.value = result.page
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  dialogMode.value = 'create'
  editingId.value = null
  form.name = ''
  form.description = ''
  dialogVisible.value = true
}

function openEditDialog(item: KnowledgeBase) {
  dialogMode.value = 'edit'
  editingId.value = item.id
  form.name = item.name
  form.description = item.description
  dialogVisible.value = true
}

async function saveItem() {
  if (!form.name.trim()) return ElMessage.warning('请输入知识库名称')
  saving.value = true
  try {
    const data = { name: form.name.trim(), description: form.description.trim() }
    if (dialogMode.value === 'create') {
      await createKnowledgeBase(data)
      page.value = 1
      ElMessage.success('创建成功')
    } else if (editingId.value !== null) {
      await updateKnowledgeBase(editingId.value, data)
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function removeItem(item: KnowledgeBase) {
  try {
    await ElMessageBox.confirm(`确定删除“${item.name}”吗？文档和对话也会删除。`, '删除确认', {
      type: 'warning',
    })
    await deleteKnowledgeBase(item.id)
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    await load()
    ElMessage.success('删除成功')
  } catch (error: unknown) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

function searchItems() {
  page.value = 1
  load()
}

function changePage(nextPage: number) {
  page.value = nextPage
  load()
}

onMounted(load)
</script>

<template>
  <AppShell
    eyebrow="KNOWLEDGE WORKSPACE"
    title="企业知识空间"
    description="沉淀团队文档，让知识经过解析、检索与引用后真正服务业务。"
  >
    <template #actions>
      <el-input
        v-model="keyword"
        class="knowledge-search"
        clearable
        placeholder="搜索知识库"
        @keyup.enter="searchItems"
        @clear="searchItems"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button :icon="Setting" @click="router.push('/model-configs')">模型服务</el-button>
      <el-button v-if="canWrite" type="primary" :icon="Plus" @click="openCreateDialog">新建知识库</el-button>
    </template>

    <section class="workspace-overview">
      <article><span class="metric-icon blue"><el-icon><Collection /></el-icon></span><div><strong>{{ total }}</strong><p>知识库总数</p></div><small>持续沉淀企业知识资产</small></article>
      <article><span class="metric-icon cyan">文</span><div><strong>{{ visibleDocumentCount }}</strong><p>当前页文档</p></div><small>结构化解析与父子切片</small></article>
      <article><span class="metric-icon green">✓</span><div><strong>{{ readyKnowledgeCount }}</strong><p>已接入内容</p></div><small>可进入检索与问答流程</small></article>
      <article><span class="metric-icon violet">R</span><div><strong>Hybrid</strong><p>检索能力</p></div><small>Vector · BM25 · Reranker</small></article>
    </section>

    <section class="content-section">
      <header class="section-heading"><div><h2>知识库列表</h2><p>选择一个知识库进入文档管理与智能问答。</p></div><span>{{ total }} 个空间</span></header>

      <div v-loading="loading" class="knowledge-grid">
        <article
          v-for="item in items"
          :key="item.id"
          class="knowledge-card"
          role="button"
          tabindex="0"
          @click="router.push(`/knowledge/${item.id}`)"
          @keyup.enter="router.push(`/knowledge/${item.id}`)"
        >
          <div class="knowledge-icon"><el-icon><Collection /></el-icon></div>
          <div class="knowledge-content">
            <span class="card-eyebrow">KNOWLEDGE BASE</span>
            <h3>{{ item.name }}</h3>
            <p>{{ item.description || '暂无描述' }}</p>
            <div class="knowledge-card-footer"><span>{{ item.document_count }} 个文档</span><strong>进入空间 →</strong></div>
          </div>
          <div v-if="canWrite" class="card-actions">
            <el-button text :icon="Edit" aria-label="编辑知识库" @click.stop="openEditDialog(item)" />
            <el-button text :icon="Delete" aria-label="删除知识库" @click.stop="removeItem(item)" />
          </div>
        </article>
        <button v-if="canWrite && !loading && !items.length && !keyword" class="empty-card" @click="openCreateDialog">
          <el-icon><Plus /></el-icon><span>创建第一个知识库</span>
        </button>
        <div v-if="!loading && !items.length && keyword" class="search-empty">没有找到匹配的知识库</div>
      </div>
      <div v-if="total" class="knowledge-pagination">
        <el-pagination
          background
          layout="total, prev, pager, next"
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          @current-change="changePage"
        />
      </div>
    </section>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="460px">
      <el-form label-position="top">
        <el-form-item label="名称"><el-input v-model="form.name" maxlength="100" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveItem">{{ dialogMode === 'create' ? '创建' : '保存' }}</el-button>
      </template>
    </el-dialog>
  </AppShell>
</template>

<style scoped>
.knowledge-search{width:260px}.workspace-overview{margin-bottom:26px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.workspace-overview article{position:relative;min-height:126px;padding:18px;display:grid;grid-template-columns:auto 1fr;align-items:center;gap:12px;background:#fff;border:1px solid #e7ecf4;border-radius:15px;box-shadow:0 8px 24px rgba(30,64,175,.035)}.metric-icon{width:39px;height:39px;display:grid;place-items:center;border-radius:11px;font-size:15px;font-weight:800}.metric-icon.blue{color:#2563eb;background:#eaf2ff}.metric-icon.cyan{color:#0284c7;background:#e8f7ff}.metric-icon.green{color:#15915f;background:#eafaf3}.metric-icon.violet{color:#7554d6;background:#f1edff}.workspace-overview strong{color:#1d293d;font-size:24px;line-height:1}.workspace-overview p{margin:5px 0 0;color:#69778c;font-size:11px}.workspace-overview small{grid-column:1/-1;color:#a0aabc;font-size:9px}.content-section{padding:22px;background:#fff;border:1px solid #e7ecf4;border-radius:17px;box-shadow:0 12px 36px rgba(30,64,175,.04)}.section-heading{margin-bottom:19px;display:flex;align-items:flex-end;justify-content:space-between;gap:18px}.section-heading h2{margin:0 0 6px;color:#253147;font-size:17px}.section-heading p{margin:0;color:#8b97a9;font-size:11px}.section-heading>span{padding:5px 9px;color:#567092;background:#f3f6fa;border-radius:20px;font-size:10px}.card-eyebrow{display:block!important;margin:0 0 7px;color:#8ba2c4!important;font-size:8px!important;font-weight:800;letter-spacing:.14em}.knowledge-card-footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.knowledge-card-footer strong{color:#2563eb;font-size:10px;font-weight:700}.knowledge-grid{grid-template-columns:repeat(auto-fill,minmax(290px,1fr))}.knowledge-card{min-height:178px}.knowledge-icon{background:linear-gradient(145deg,#edf5ff,#e4eeff)}
@media(max-width:1180px){.workspace-overview{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){.workspace-overview{grid-template-columns:1fr}.knowledge-search{width:100%}.content-section{padding:16px}}
</style>
