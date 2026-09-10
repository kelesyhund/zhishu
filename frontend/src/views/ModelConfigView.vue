<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Delete, Edit, Key, Plus } from '@element-plus/icons-vue'

import {
  createModelConfig,
  deleteModelConfig,
  listModelConfigs,
  testModelConfig,
  updateModelConfig,
  type ModelConfigItem,
  type ModelConfigWrite,
  type ModelType,
} from '../api'
import { errorMessage } from '../api/client'
import AppShell from '../components/AppShell.vue'
import { useWorkspaceStore } from '../stores/workspace'

const activeType = ref<ModelType>('CHAT')
const workspaceStore = useWorkspaceStore()
const items = ref<ModelConfigItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const testingConfigId = ref<number | null>(null)
const deletingConfigId = ref<number | null>(null)
const form = reactive({
  name: '',
  base_url: '',
  model_name: '',
  api_key: '',
  timeout_seconds: 30,
})

async function load(nextPage = page.value) {
  loading.value = true
  try {
    const result = await listModelConfigs(activeType.value, nextPage, pageSize.value)
    items.value = result.items
    total.value = result.total
    page.value = result.page
    pageSize.value = result.page_size
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.base_url = ''
  form.model_name = ''
  form.api_key = ''
  form.timeout_seconds = 30
}

function openCreate() {
  editingId.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(item: ModelConfigItem) {
  editingId.value = item.id
  form.name = item.name
  form.base_url = item.base_url
  form.model_name = item.model_name
  form.api_key = ''
  form.timeout_seconds = item.timeout_seconds
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim() || !form.base_url.trim() || !form.model_name.trim()) {
    return ElMessage.warning('请填写配置名称、模型地址和模型名称')
  }
  if (editingId.value === null && !form.api_key.trim()) {
    return ElMessage.warning('新建配置必须填写API Key')
  }
  saving.value = true
  try {
    const payload: ModelConfigWrite = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model_name: form.model_name.trim(),
      timeout_seconds: form.timeout_seconds,
    }
    if (editingId.value === null) {
      payload.model_type = activeType.value
      payload.api_key = form.api_key.trim()
      await createModelConfig(payload)
      page.value = 1
      ElMessage.success('模型配置创建成功')
    } else {
      if (form.api_key.trim()) payload.api_key = form.api_key.trim()
      await updateModelConfig(editingId.value, payload)
      ElMessage.success('模型配置修改成功')
    }
    dialogVisible.value = false
    await load(page.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    form.api_key = ''
    saving.value = false
  }
}

async function testConnection(item: ModelConfigItem) {
  if (testingConfigId.value !== null) return
  testingConfigId.value = item.id
  try {
    const result = await testModelConfig(item.id)
    const dimension = result.embedding_dimension ? `，向量维度 ${result.embedding_dimension}` : ''
    ElMessage.success(`连接成功，耗时 ${result.latency_ms}ms${dimension}`)
    await load(page.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
    await load(page.value)
  } finally {
    testingConfigId.value = null
  }
}

async function remove(item: ModelConfigItem) {
  if (deletingConfigId.value !== null) return
  try {
    await ElMessageBox.confirm(
      `确认删除模型配置“${item.name}”吗？正在被知识库使用的配置不能删除。`,
      '删除模型配置',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deletingConfigId.value = item.id
  try {
    await deleteModelConfig(item.id)
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    await load(page.value)
    ElMessage.success('模型配置已删除')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    deletingConfigId.value = null
  }
}

function testStatusText(item: ModelConfigItem) {
  if (item.last_test_status === 'SUCCESS') return item.last_test_message || '连接成功'
  if (item.last_test_status === 'FAILURE') return item.last_test_message || '连接失败'
  return '尚未测试'
}

function testStatusType(item: ModelConfigItem): 'success' | 'danger' | 'info' {
  if (item.last_test_status === 'SUCCESS') return 'success'
  if (item.last_test_status === 'FAILURE') return 'danger'
  return 'info'
}

watch(activeType, () => {
  page.value = 1
  void load(1)
})

onMounted(() => load(1))
</script>

<template>
  <AppShell
    eyebrow="MODEL OPERATIONS"
    title="模型服务"
    description="集中管理 Chat 与 Embedding 服务，安全控制连接、密钥和版本。"
  >
    <template #actions>
      <el-button v-if="workspaceStore.can('model.manage')" type="primary" :icon="Plus" @click="openCreate">
        新建{{ activeType === 'CHAT' ? 'Chat' : 'Embedding' }}配置
      </el-button>
    </template>

    <section class="model-overview">
      <div><span>当前类型</span><strong>{{ activeType === 'CHAT' ? 'Chat 生成模型' : 'Embedding 向量模型' }}</strong><small>两类模型独立配置、独立绑定</small></div>
      <div><span>配置数量</span><strong>{{ total }}</strong><small>仅展示当前账号可访问配置</small></div>
      <div><span>密钥策略</span><strong>Fernet 加密</strong><small>查询接口只返回脱敏结果</small></div>
    </section>

    <section class="model-content-card">
      <header class="model-section-header">
        <div><h2>模型配置列表</h2><p>连接测试不会创建会话，也不会保存测试向量。</p></div>
        <el-tag effect="plain" type="success">安全存储已启用</el-tag>
      </header>

      <el-tabs v-model="activeType" class="model-type-tabs">
        <el-tab-pane label="Chat 模型" name="CHAT" />
        <el-tab-pane label="Embedding 模型" name="EMBEDDING" />
      </el-tabs>

      <div v-loading="loading" class="model-config-list">
        <el-empty v-if="!loading && !items.length" description="还没有模型配置" />
        <article v-for="item in items" :key="item.id" class="model-config-card">
          <div class="model-config-main">
            <div class="model-config-title">
              <el-icon><Key /></el-icon>
              <strong>{{ item.name }}</strong>
              <el-tag size="small" effect="plain">{{ item.model_type }}</el-tag>
            </div>
            <dl class="model-config-fields">
              <div><dt>模型</dt><dd>{{ item.model_name }}</dd></div>
              <div><dt>地址</dt><dd>{{ item.base_url }}</dd></div>
              <div><dt>密钥</dt><dd>{{ item.api_key_masked }}</dd></div>
              <div><dt>超时</dt><dd>{{ item.timeout_seconds }} 秒</dd></div>
            </dl>
            <div class="model-test-status">
              <el-tag size="small" :type="testStatusType(item)">{{ testStatusText(item) }}</el-tag>
              <span v-if="item.last_test_at">{{ new Date(item.last_test_at).toLocaleString() }}</span>
              <span v-if="item.embedding_dimension">维度 {{ item.embedding_dimension }}</span>
              <span>版本 {{ item.revision }}</span>
            </div>
          </div>
          <div v-if="workspaceStore.can('model.manage')" class="model-config-actions">
            <el-button
              :icon="Connection"
              :loading="testingConfigId === item.id"
              :disabled="testingConfigId !== null || deletingConfigId !== null"
              @click="testConnection(item)"
            >测试连接</el-button>
            <el-button :icon="Edit" :disabled="testingConfigId !== null" @click="openEdit(item)">编辑</el-button>
            <el-button
              type="danger"
              plain
              :icon="Delete"
              :loading="deletingConfigId === item.id"
              :disabled="deletingConfigId !== null || testingConfigId !== null"
              @click="remove(item)"
            >删除</el-button>
          </div>
        </article>
      </div>

      <div v-if="total" class="knowledge-pagination">
        <el-pagination
          v-model:current-page="page"
          background
          layout="total, prev, pager, next"
          :page-size="pageSize"
          :total="total"
          @current-change="load"
        />
      </div>
    </section>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId === null ? '新建模型配置' : '编辑模型配置'"
      width="560px"
      @closed="form.api_key = ''"
    >
      <el-form label-position="top">
        <el-form-item label="配置名称"><el-input v-model="form.name" maxlength="100" /></el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" placeholder="https://api.example.com/v1" />
        </el-form-item>
        <el-form-item label="模型名称"><el-input v-model="form.model_name" /></el-form-item>
        <el-form-item :label="editingId === null ? 'API Key' : 'API Key（留空表示保留原密钥）'">
          <el-input v-model="form.api_key" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="超时时间">
          <el-input-number v-model="form.timeout_seconds" :min="1" :max="120" />
          <span class="timeout-unit">秒</span>
        </el-form-item>
      </el-form>
      <el-alert
        title="密钥提交后只显示脱敏结果，编辑页面不会回填明文。"
        type="info"
        :closable="false"
        show-icon
      />
      <template #footer>
        <el-button :disabled="saving" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </AppShell>
</template>

<style scoped>
.model-overview{margin-bottom:22px;display:grid;grid-template-columns:1.2fr .7fr 1fr;gap:14px}.model-overview>div{padding:19px 20px;background:#fff;border:1px solid #e7ecf4;border-radius:15px;box-shadow:0 8px 24px rgba(30,64,175,.035)}.model-overview span,.model-overview small{display:block}.model-overview span{color:#8391a6;font-size:10px;font-weight:700;letter-spacing:.08em}.model-overview strong{display:block;margin:10px 0 7px;color:#263349;font-size:18px}.model-overview small{color:#a0aabc;font-size:9px}.model-content-card{padding:22px;background:#fff;border:1px solid #e7ecf4;border-radius:17px;box-shadow:0 12px 36px rgba(30,64,175,.04)}.model-section-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.model-section-header h2{margin:0 0 6px;color:#253147;font-size:17px}.model-section-header p{margin:0;color:#8b97a9;font-size:11px}.model-type-tabs{margin-top:17px}.model-config-card{border-color:#e5ebf4;border-radius:14px;box-shadow:none;transition:.18s ease}.model-config-card:hover{border-color:#bdd2f5;box-shadow:0 10px 28px rgba(37,99,235,.07);transform:translateY(-1px)}
@media(max-width:850px){.model-overview{grid-template-columns:1fr}.model-config-card{flex-direction:column}.model-config-actions{width:100%;justify-content:flex-start}}
</style>
