<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Collection, Connection, DataAnalysis, List, Lock, OfficeBuilding, Promotion, SetUp, SwitchButton } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '../stores/auth'
import { useWorkspaceStore } from '../stores/workspace'
import BrandLogo from './BrandLogo.vue'
import { logout as logoutApi } from '../api'

defineProps<{
  eyebrow?: string
  title: string
  description: string
}>()

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const workspaceStore = useWorkspaceStore()
const navItems = [
  { label: '工作台', caption: '经营状态与待办', path: '/dashboard', icon: DataAnalysis },
  { label: '知识空间', caption: '文档与智能问答', path: '/knowledge', icon: Collection },
  { label: 'AI 应用', caption: '配置、发布与接入', path: '/applications', icon: Promotion },
  { label: '模型服务', caption: '模型与密钥管理', path: '/model-configs', icon: Connection },
  { label: '组织治理', caption: '成员、空间与审计', path: '/organization', icon: OfficeBuilding },
  { label: '任务中心', caption: '跨知识库处理任务', path: '/tasks', icon: List },
  { label: '向量索引', caption: '迁移、覆盖与检索状态', path: '/operations/vector-index', icon: SetUp, capability: 'vector.manage' },
  { label: '账号安全', caption: '密码与登录设备', path: '/account/security', icon: Lock },
]
const visibleNavItems = computed(() => navItems.filter((item) => !item.capability || workspaceStore.can(item.capability)))
const activePath = computed(() => {
  if (route.path.startsWith('/applications')) return '/applications'
  if (route.path.startsWith('/model-configs')) return '/model-configs'
  if (route.path.startsWith('/organization')) return '/organization'
  if (route.path.startsWith('/dashboard')) return '/dashboard'
  if (route.path.startsWith('/tasks')) return '/tasks'
  if (route.path.startsWith('/operations/vector-index')) return '/operations/vector-index'
  if (route.path.startsWith('/account/security')) return '/account/security'
  return '/knowledge'
})

async function logout() {
  try { await logoutApi() } catch { /* 本地状态仍需清理 */ }
  workspaceStore.reset()
  auth.logout()
  void router.push('/login')
}

function switchWorkspace(value: string | number) {
  workspaceStore.switchWorkspace(Number(value))
}

onMounted(async () => {
  try {
    await workspaceStore.initialize()
  } catch {
    ElMessage.error('工作空间上下文加载失败，请重新登录')
  }
})
</script>

<template>
  <div class="zhishu-shell">
    <aside class="zhishu-sidebar">
      <div class="sidebar-brand"><BrandLogo /></div>
      <div class="workspace-label"><span class="workspace-dot" />企业知识工作台</div>
      <el-select
        class="workspace-selector"
        :model-value="workspaceStore.activeWorkspaceId || undefined"
        :loading="workspaceStore.loading"
        placeholder="选择工作空间"
        @change="switchWorkspace"
      >
        <el-option-group
          v-for="organization in workspaceStore.organizations"
          :key="organization.id"
          :label="organization.name"
        >
          <el-option
            v-for="workspace in workspaceStore.workspaces.filter((item) => item.organization_id === organization.id)"
            :key="workspace.id"
            :label="`${workspace.name} · ${workspace.current_user_role}`"
            :value="workspace.id"
          />
        </el-option-group>
      </el-select>
      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in visibleNavItems"
          :key="item.path"
          class="nav-item"
          :class="{ active: activePath === item.path }"
          @click="router.push(item.path)"
        >
          <span class="nav-icon"><el-icon><component :is="item.icon" /></el-icon></span>
          <span><strong>{{ item.label }}</strong><small>{{ item.caption }}</small></span>
        </button>
      </nav>
      <div class="sidebar-insight">
        <span>可信知识</span>
        <strong>让每一次回答都有据可循</strong>
        <p>统一管理文档、检索、模型与应用。</p>
      </div>
      <div class="sidebar-user">
        <span class="user-avatar">{{ auth.username.slice(0, 1).toUpperCase() }}</span>
        <span class="user-copy"><strong>{{ auth.username }}</strong><small>当前账号</small></span>
        <el-button text circle :icon="SwitchButton" aria-label="退出登录" @click="logout" />
      </div>
    </aside>

    <section class="zhishu-main">
      <header class="shell-header">
        <div class="shell-heading">
          <span>{{ eyebrow || 'ENTERPRISE KNOWLEDGE' }}</span>
          <h1>{{ title }}</h1>
          <p>{{ description }}</p>
        </div>
        <div class="shell-actions"><slot name="actions" /></div>
      </header>
      <main class="shell-content"><slot /></main>
    </section>
  </div>
</template>

<style scoped>
.zhishu-shell{min-height:100vh;display:grid;grid-template-columns:248px minmax(0,1fr);background:#f6f8fc}.zhishu-sidebar{position:sticky;top:0;height:100vh;padding:25px 18px 18px;display:flex;flex-direction:column;background:rgba(255,255,255,.96);border-right:1px solid #e8edf5;box-shadow:8px 0 32px rgba(30,64,175,.035);z-index:10}.sidebar-brand{padding:0 8px 22px}.workspace-label{margin:0 7px 8px;display:flex;align-items:center;gap:8px;color:#91a0b7;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.workspace-dot{width:7px;height:7px;background:#22c55e;border:2px solid #dcfce7;border-radius:50%}.workspace-selector{width:100%;margin-bottom:14px}.sidebar-nav{min-height:0;flex:1;display:grid;align-content:start;gap:7px;padding-right:4px;overflow-y:auto;scrollbar-width:thin}.nav-item{width:100%;padding:11px 12px;display:flex;align-items:center;gap:11px;color:#64748b;text-align:left;background:transparent;border:1px solid transparent;border-radius:12px;cursor:pointer;transition:.18s ease}.nav-item:hover{color:#2454c6;background:#f5f8ff}.nav-item.active{color:#174db7;background:linear-gradient(100deg,#eef5ff,#f7faff);border-color:#dce9ff;box-shadow:0 7px 20px rgba(37,99,235,.07)}.nav-icon{width:34px;height:34px;display:grid;place-items:center;color:inherit;background:#f4f7fb;border-radius:10px;font-size:17px}.nav-item.active .nav-icon{color:#fff;background:#2563eb;box-shadow:0 6px 13px rgba(37,99,235,.24)}.nav-item strong,.nav-item small{display:block}.nav-item strong{font-size:13px}.nav-item small{margin-top:5px;color:#9aa7ba;font-size:10px}.sidebar-insight{margin-top:16px;padding:17px;background:linear-gradient(145deg,#0f4fc2,#2563eb 65%,#3b82f6);border-radius:15px;color:#fff;box-shadow:0 14px 30px rgba(37,99,235,.2)}.sidebar-insight span{font-size:10px;letter-spacing:.16em;opacity:.7}.sidebar-insight strong{display:block;margin-top:9px;font-size:13px;line-height:1.55}.sidebar-insight p{margin:7px 0 0;color:rgba(255,255,255,.7);font-size:10px;line-height:1.55}.sidebar-user{margin-top:16px;padding:11px 6px 0;display:flex;align-items:center;gap:9px;border-top:1px solid #edf0f5}.user-avatar{width:32px;height:32px;display:grid;place-items:center;color:#2358c5;background:#eaf2ff;border-radius:10px;font-size:12px;font-weight:800}.user-copy{min-width:0;flex:1}.user-copy strong,.user-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.user-copy strong{color:#344054;font-size:12px}.user-copy small{margin-top:3px;color:#9aa5b5;font-size:9px}.zhishu-main{min-width:0}.shell-header{min-height:147px;padding:32px clamp(28px,4vw,58px) 27px;display:flex;align-items:flex-end;justify-content:space-between;gap:24px;background:linear-gradient(115deg,#fff 0%,#fbfdff 68%,#edf5ff 100%);border-bottom:1px solid #e8edf5}.shell-heading>span{color:#2563eb;font-size:10px;font-weight:800;letter-spacing:.18em}.shell-heading h1{margin:8px 0 7px;color:#172033;font-size:28px;line-height:1.2;letter-spacing:-.02em}.shell-heading p{margin:0;color:#78869a;font-size:13px}.shell-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}.shell-content{max-width:1480px;margin:0 auto;padding:28px clamp(28px,4vw,58px) 48px}
@media(max-width:900px){.zhishu-shell{display:block}.zhishu-sidebar{position:sticky;height:auto;padding:12px 18px;display:flex;flex-direction:row;align-items:center;border-right:0;border-bottom:1px solid #e8edf5}.sidebar-brand{padding:0}.workspace-label,.sidebar-insight,.sidebar-user .user-copy{display:none}.sidebar-nav{min-height:auto;flex:none;margin-left:auto;display:flex;padding-right:0;overflow:visible}.nav-item{width:auto;padding:8px}.nav-item>span:last-child{display:none}.nav-icon{width:32px;height:32px}.sidebar-user{margin:0 0 0 8px;padding:0;border:0}.shell-header{min-height:auto;padding:25px 22px 22px;align-items:flex-start;flex-direction:column}.shell-actions{width:100%;justify-content:flex-start}.shell-content{padding:22px}}
</style>
