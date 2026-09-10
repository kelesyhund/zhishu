<template>
  <AppShell
    eyebrow="AI APPLICATIONS"
    title="智能应用"
    description="将知识、模型与安全策略组合成可发布、可集成的企业应用。"
  >
    <template #actions>
      <el-input v-model="keyword" class="application-search" clearable placeholder="搜索应用" @keyup.enter="load">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button v-if="workspaceStore.can('application.write')" type="primary" :icon="Plus" @click="createDialog = true">创建应用</el-button>
    </template>

    <section class="application-overview">
      <div><span>应用总数</span><strong>{{ total }}</strong><small>草稿、发布与停用状态统一管理</small></div>
      <div><span>本页已发布</span><strong>{{ publishedCount }}</strong><small>使用不可变版本快照对外提供服务</small></div>
      <div><span>接入方式</span><strong>Web · API</strong><small>公开链接、嵌入页面与兼容接口</small></div>
    </section>

    <section class="application-content-card">
      <header class="application-section-head"><div><h2>应用列表</h2><p>从草稿配置开始，预览确认后再发布正式版本。</p></div><el-button plain @click="load">刷新列表</el-button></header>
      <div v-loading="loading" class="application-grid">
        <article v-for="application in applications" :key="application.id" class="application-card">
          <header>
            <span class="application-icon"><el-icon><Promotion /></el-icon></span>
            <el-tag :type="statusType(application.status)" effect="light">{{ statusLabel(application.status) }}</el-tag>
          </header>
          <span class="application-eyebrow">AI APPLICATION</span>
          <h3>{{ application.name }}</h3>
          <p>{{ application.description || '暂无应用说明，可进入编辑页面补充业务定位。' }}</p>
          <dl>
            <div><dt>知识库</dt><dd>{{ application.knowledge_bases.length }} 个</dd></div>
            <div><dt>正式版本</dt><dd>{{ application.current_published_version_number ? `v${application.current_published_version_number}` : '未发布' }}</dd></div>
          </dl>
          <footer>
            <el-button v-if="workspaceStore.can('application.write')" type="primary" plain :icon="Edit" @click="router.push(`/applications/${application.id}`)">配置</el-button>
            <el-button :icon="View" @click="router.push(`/applications/${application.id}/overview`)">发布概览</el-button>
            <el-button v-if="workspaceStore.can('application.write')" text type="danger" :icon="Delete" aria-label="删除应用" @click="remove(application)" />
          </footer>
        </article>
        <button v-if="workspaceStore.can('application.write') && !loading && applications.length === 0" class="application-empty" @click="createDialog = true">
          <span class="application-icon"><el-icon><Plus /></el-icon></span><strong>创建第一个智能应用</strong><small>组合知识库并配置专属问答体验</small>
        </button>
      </div>
      <el-pagination
        v-if="total > pageSize"
        v-model:current-page="pageNumber"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        @current-change="load"
      />
    </section>

    <el-dialog v-model="createDialog" title="创建AI应用" width="480px">
      <el-form label-position="top">
        <el-form-item label="应用名称"><el-input v-model="newApplication.name" maxlength="100" /></el-form-item>
        <el-form-item label="应用说明"><el-input v-model="newApplication.description" type="textarea" maxlength="1000" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建并配置</el-button>
      </template>
    </el-dialog>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Plus, Promotion, Search, View } from '@element-plus/icons-vue'
import {
  createApplication,
  deleteApplication,
  disableApplication,
  listApplications,
  type ApplicationItem,
  type ApplicationStatus,
} from '../api'
import AppShell from '../components/AppShell.vue'
import { useWorkspaceStore } from '../stores/workspace'

const router = useRouter()
const workspaceStore = useWorkspaceStore()
const applications = ref<ApplicationItem[]>([])
const loading = ref(false)
const creating = ref(false)
const keyword = ref('')
const pageNumber = ref(1)
const pageSize = 20
const total = ref(0)
const createDialog = ref(false)
const newApplication = reactive({ name: '', description: '' })
const publishedCount = computed(() => applications.value.filter((item) => item.status === 'PUBLISHED').length)

function messageOf(error: unknown) {
  const value = error as { response?: { data?: { message?: string } }; message?: string }
  return value.response?.data?.message || value.message || '操作失败'
}
function statusLabel(status: ApplicationStatus) {
  return { DRAFT: '草稿', PUBLISHED: '已发布', DISABLED: '已停用' }[status]
}
function statusType(status: ApplicationStatus) {
  return status === 'PUBLISHED' ? 'success' : status === 'DISABLED' ? 'info' : 'warning'
}
async function load() {
  loading.value = true
  try {
    const result = await listApplications(pageNumber.value, pageSize, keyword.value)
    applications.value = result.items
    total.value = result.total
  } catch (error) { ElMessage.error(messageOf(error)) } finally { loading.value = false }
}
async function create() {
  if (!newApplication.name.trim()) return ElMessage.warning('请输入应用名称')
  creating.value = true
  try {
    const application = await createApplication({ name: newApplication.name, description: newApplication.description })
    createDialog.value = false
    await router.push(`/applications/${application.id}`)
  } catch (error) { ElMessage.error(messageOf(error)) } finally { creating.value = false }
}
async function remove(application: ApplicationItem) {
  try {
    await ElMessageBox.confirm('删除应用会清理应用版本、凭证和应用会话，但不会删除知识库。是否继续？', '删除应用', { type: 'warning' })
    if (application.status === 'PUBLISHED') await disableApplication(application.id)
    await deleteApplication(application.id)
    ElMessage.success('应用已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(messageOf(error))
  }
}
onMounted(load)
</script>

<style scoped>
.application-search{width:260px}.application-overview{margin-bottom:22px;display:grid;grid-template-columns:.7fr 1.1fr 1fr;gap:14px}.application-overview>div{padding:19px 20px;background:#fff;border:1px solid #e7ecf4;border-radius:15px;box-shadow:0 8px 24px rgba(30,64,175,.035)}.application-overview span,.application-overview small{display:block}.application-overview span{color:#8391a6;font-size:10px;font-weight:700;letter-spacing:.08em}.application-overview strong{display:block;margin:10px 0 7px;color:#263349;font-size:19px}.application-overview small{color:#a0aabc;font-size:9px}.application-content-card{padding:22px;background:#fff;border:1px solid #e7ecf4;border-radius:17px;box-shadow:0 12px 36px rgba(30,64,175,.04)}.application-section-head{margin-bottom:19px;display:flex;align-items:center;justify-content:space-between;gap:16px}.application-section-head h2{margin:0 0 6px;color:#253147;font-size:17px}.application-section-head p{margin:0;color:#8b97a9;font-size:11px}.application-grid{min-height:260px;display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:15px}.application-card,.application-empty{min-height:275px;padding:19px;display:flex;flex-direction:column;text-align:left;background:#fff;border:1px solid #e5ebf4;border-radius:15px;transition:.18s ease}.application-card:hover{border-color:#bcd2f6;box-shadow:0 12px 30px rgba(37,99,235,.08);transform:translateY(-2px)}.application-card header{display:flex;align-items:center;justify-content:space-between}.application-icon{width:42px;height:42px;display:grid;place-items:center;color:#fff;background:linear-gradient(145deg,#2563eb,#3b82f6);border-radius:12px;box-shadow:0 8px 17px rgba(37,99,235,.2)}.application-eyebrow{margin-top:19px;color:#8ba2c4;font-size:8px;font-weight:800;letter-spacing:.16em}.application-card h3{margin:7px 0 8px;color:#253147;font-size:17px}.application-card>p{min-height:39px;margin:0;color:#7f8da1;font-size:11px;line-height:1.7}.application-card dl{margin:17px 0;padding:11px 0;display:grid;grid-template-columns:1fr 1fr;border-top:1px solid #edf0f5;border-bottom:1px solid #edf0f5}.application-card dl div+div{padding-left:16px;border-left:1px solid #edf0f5}.application-card dt{color:#9ba6b6;font-size:9px}.application-card dd{margin:5px 0 0;color:#435169;font-size:11px;font-weight:700}.application-card footer{margin-top:auto;display:flex;align-items:center;gap:5px}.application-card footer .el-button+.el-button{margin-left:0}.application-empty{align-items:center;justify-content:center;gap:10px;color:#73839a;border-style:dashed;cursor:pointer}.application-empty strong{font-size:13px}.application-empty small{font-size:10px}.el-pagination{margin-top:20px;justify-content:flex-end}
@media(max-width:800px){.application-overview{grid-template-columns:1fr}.application-search{width:100%}.application-content-card{padding:16px}}
</style>
