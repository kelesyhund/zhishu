<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, UserFilled } from '@element-plus/icons-vue'

import AppShell from '../components/AppShell.vue'
import { errorMessage } from '../api/client'
import {
  addOrganizationMember,
  addWorkspaceMember,
  createOrganization,
  createInvitation,
  invitationAction,
  listInvitations,
  createWorkspace,
  listAuditEvents,
  listOrganizationMembers,
  listOrganizationWorkspaces,
  listWorkspaceMembers,
  removeOrganizationMember,
  removeWorkspaceMember,
  updateOrganizationMember,
  updateWorkspaceMember,
  type AuditEventItem,
  type OrganizationMembershipItem,
  type InvitationItem,
  type OrganizationRole,
  type WorkspaceItem,
  type WorkspaceMembershipItem,
  type WorkspaceRole,
} from '../api'
import { useWorkspaceStore } from '../stores/workspace'

const workspaceStore = useWorkspaceStore()
const activeTab = ref('organization-members')
const selectedOrganizationId = ref<number | null>(null)
const selectedWorkspaceId = ref<number | null>(null)
const loading = ref(false)
const organizationMembers = ref<OrganizationMembershipItem[]>([])
const workspaces = ref<WorkspaceItem[]>([])
const workspaceMembers = ref<WorkspaceMembershipItem[]>([])
const auditEvents = ref<AuditEventItem[]>([])
const invitations = ref<InvitationItem[]>([])
const invitationDialog = ref(false)
const invitationLink = ref('')
const organizationDialog = ref(false)
const workspaceDialog = ref(false)
const memberDialog = ref<'organization' | 'workspace' | null>(null)
const form = reactive({ name: '', username: '', email: '', organizationRole: 'MEMBER' as OrganizationRole, workspaceRole: 'VIEWER' as WorkspaceRole })
const currentOrganization = computed(() => workspaceStore.organizations.find((item) => item.id === selectedOrganizationId.value) || null)
const canManageOrganization = computed(() => ['OWNER', 'ADMIN'].includes(currentOrganization.value?.current_user_role || ''))
const canAudit = computed(() => workspaceStore.can('audit.read'))

async function loadAll() {
  loading.value = true
  try {
    await workspaceStore.initialize(true)
    if (!selectedOrganizationId.value || !workspaceStore.organizations.some((item) => item.id === selectedOrganizationId.value)) {
      selectedOrganizationId.value = workspaceStore.activeWorkspace?.organization_id || workspaceStore.organizations[0]?.id || null
    }
    if (!selectedOrganizationId.value) return
    const [memberPage, workspacePage] = await Promise.all([
      listOrganizationMembers(selectedOrganizationId.value),
      listOrganizationWorkspaces(selectedOrganizationId.value),
    ])
    organizationMembers.value = memberPage.items
    workspaces.value = workspacePage.items
    if (!selectedWorkspaceId.value || !workspaces.value.some((item) => item.id === selectedWorkspaceId.value)) {
      selectedWorkspaceId.value = workspaceStore.activeWorkspace?.organization_id === selectedOrganizationId.value
        ? workspaceStore.activeWorkspace.id
        : workspaces.value[0]?.id || null
    }
    await loadWorkspaceMembers()
    if (canAudit.value) auditEvents.value = (await listAuditEvents({ page_size: 100, all_workspaces: 'true' })).items
    else auditEvents.value = []
    invitations.value = canManageOrganization.value ? (await listInvitations(selectedOrganizationId.value)).items : []
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function submitInvitation() {
  if (!selectedOrganizationId.value || !form.email.trim()) return
  try {
    const item = await createInvitation(selectedOrganizationId.value, {
      email: form.email.trim(), organization_role: form.organizationRole,
      workspace_grants: selectedWorkspaceId.value ? [{ workspace_id: selectedWorkspaceId.value, role: form.workspaceRole }] : [],
    })
    invitationLink.value = item.invitation_url || ''
    form.email = ''
    await loadAll()
    ElMessage.success('邀请已创建；邀请链接仅在本次显示')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function actInvitation(item: InvitationItem, action: 'revoke' | 'resend') {
  if (!selectedOrganizationId.value) return
  try {
    const result = await invitationAction(selectedOrganizationId.value, item.id, action)
    invitationLink.value = result.invitation_url || ''
    await loadAll()
    ElMessage.success(action === 'revoke' ? '邀请已撤销' : '邀请链接已重新生成')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function loadWorkspaceMembers() {
  if (!selectedWorkspaceId.value) return (workspaceMembers.value = [])
  try {
    workspaceMembers.value = (await listWorkspaceMembers(selectedWorkspaceId.value)).items
  } catch {
    workspaceMembers.value = []
  }
}

async function submitOrganization() {
  if (!form.name.trim()) return
  try {
    const item = await createOrganization(form.name.trim())
    organizationDialog.value = false
    form.name = ''
    await workspaceStore.initialize(true)
    selectedOrganizationId.value = item.id
    await loadAll()
    ElMessage.success('组织与默认工作空间已创建')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function submitWorkspace() {
  if (!selectedOrganizationId.value || !form.name.trim()) return
  try {
    await createWorkspace(selectedOrganizationId.value, form.name.trim())
    workspaceDialog.value = false
    form.name = ''
    await loadAll()
    ElMessage.success('工作空间已创建')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function submitMember() {
  if (!memberDialog.value || !form.username.trim()) return
  try {
    if (memberDialog.value === 'organization' && selectedOrganizationId.value) {
      await addOrganizationMember(selectedOrganizationId.value, form.username.trim(), form.organizationRole)
    } else if (memberDialog.value === 'workspace' && selectedWorkspaceId.value) {
      await addWorkspaceMember(selectedWorkspaceId.value, form.username.trim(), form.workspaceRole)
    }
    memberDialog.value = null
    form.username = ''
    await loadAll()
    ElMessage.success('成员已添加')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function changeOrganizationRole(item: OrganizationMembershipItem, role: OrganizationRole) {
  if (!selectedOrganizationId.value) return
  try {
    await updateOrganizationMember(selectedOrganizationId.value, item.id, role)
    await loadAll()
    ElMessage.success('组织角色已更新')
  } catch (error) { ElMessage.error(errorMessage(error)); await loadAll() }
}

async function changeWorkspaceRole(item: WorkspaceMembershipItem, role: WorkspaceRole) {
  if (!selectedWorkspaceId.value) return
  try {
    await updateWorkspaceMember(selectedWorkspaceId.value, item.id, role)
    await loadWorkspaceMembers()
    ElMessage.success('工作空间角色已更新')
  } catch (error) { ElMessage.error(errorMessage(error)); await loadWorkspaceMembers() }
}

async function removeMember(scope: 'organization' | 'workspace', id: number, username: string) {
  try {
    await ElMessageBox.confirm(`确认移除成员“${username}”吗？`, '移除成员', { type: 'warning' })
    if (scope === 'organization' && selectedOrganizationId.value) await removeOrganizationMember(selectedOrganizationId.value, id)
    if (scope === 'workspace' && selectedWorkspaceId.value) await removeWorkspaceMember(selectedWorkspaceId.value, id)
    await loadAll()
    ElMessage.success('成员已移除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  }
}

function resultType(result: AuditEventItem['result']) {
  return result === 'SUCCESS' ? 'success' : result === 'REJECTED' ? 'warning' : 'danger'
}

watch(selectedOrganizationId, () => { if (!loading.value) void loadAll() })
watch(selectedWorkspaceId, () => { if (!loading.value) void loadWorkspaceMembers() })
onMounted(loadAll)
</script>

<template>
  <AppShell eyebrow="GOVERNANCE & SECURITY" title="组织治理" description="以工作空间隔离企业知识资产，用角色与审计建立可追溯的协作边界。">
    <template #actions>
      <el-select v-model="selectedOrganizationId" placeholder="选择组织" style="width:220px">
        <el-option v-for="item in workspaceStore.organizations" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
      <el-button type="primary" :icon="Plus" @click="organizationDialog = true">新建组织</el-button>
    </template>

    <section class="governance-summary">
      <article><span>当前组织</span><strong>{{ currentOrganization?.name || '未选择' }}</strong><small>{{ currentOrganization?.current_user_role || '-' }}</small></article>
      <article><span>组织成员</span><strong>{{ organizationMembers.length }}</strong><small>账号级协作关系</small></article>
      <article><span>工作空间</span><strong>{{ workspaces.length }}</strong><small>业务资源隔离边界</small></article>
      <article><span>当前能力</span><strong>{{ workspaceStore.capabilities.size }}</strong><small>由有效角色集中映射</small></article>
    </section>

    <section v-loading="loading" class="governance-panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="组织成员" name="organization-members">
          <div class="pane-actions"><p>管理组织级成员；最后一名 OWNER 受事务保护。</p><el-button v-if="canManageOrganization" type="primary" :icon="UserFilled" @click="memberDialog = 'organization'">添加组织成员</el-button></div>
          <el-table :data="organizationMembers">
            <el-table-column prop="username" label="账号" min-width="180" />
            <el-table-column label="角色" width="180">
              <template #default="{ row }"><el-select :model-value="row.role" :disabled="!canManageOrganization" @change="changeOrganizationRole(row, $event)"><el-option v-for="role in ['OWNER','ADMIN','MEMBER','AUDITOR']" :key="role" :value="role" :label="role" /></el-select></template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" min-width="180" />
            <el-table-column label="操作" width="100"><template #default="{ row }"><el-button v-if="canManageOrganization" link type="danger" @click="removeMember('organization', row.id, row.username)">移除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="工作空间" name="workspaces">
          <div class="pane-actions"><p>知识库、模型配置与应用都归属于一个工作空间。</p><el-button v-if="canManageOrganization" type="primary" :icon="Plus" @click="workspaceDialog = true">新建工作空间</el-button></div>
          <el-table :data="workspaces" @row-click="(row: WorkspaceItem) => selectedWorkspaceId = row.id">
            <el-table-column prop="name" label="名称" min-width="180" />
            <el-table-column prop="slug" label="标识" min-width="150" />
            <el-table-column prop="current_user_role" label="我的角色" width="130" />
            <el-table-column prop="member_count" label="显式成员" width="100" />
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ row.status }}</el-tag></template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="空间成员" name="workspace-members">
          <div class="pane-actions"><el-select v-model="selectedWorkspaceId" style="width:220px"><el-option v-for="item in workspaces" :key="item.id" :label="item.name" :value="item.id" /></el-select><el-button v-if="workspaceStore.can('member.manage')" type="primary" @click="memberDialog = 'workspace'">添加空间成员</el-button></div>
          <el-table :data="workspaceMembers">
            <el-table-column prop="username" label="账号" min-width="180" />
            <el-table-column label="角色" width="190"><template #default="{ row }"><el-select :model-value="row.role" :disabled="!workspaceStore.can('member.manage')" @change="changeWorkspaceRole(row, $event)"><el-option v-for="role in ['ADMIN','DEVELOPER','OPERATOR','VIEWER','AUDITOR']" :key="role" :value="role" :label="role" /></el-select></template></el-table-column>
            <el-table-column prop="updated_at" label="更新时间" min-width="180" />
            <el-table-column label="操作" width="100"><template #default="{ row }"><el-button v-if="workspaceStore.can('member.manage')" link type="danger" @click="removeMember('workspace', row.id, row.username)">移除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="审计日志" name="audit" :disabled="!canAudit">
          <div class="pane-actions"><p>审计事件仅可查询，业务 API 不提供修改或删除能力。</p></div>
          <el-table :data="auditEvents">
            <el-table-column prop="created_at" label="时间" min-width="175" />
            <el-table-column prop="actor_username" label="操作者" width="130" />
            <el-table-column prop="action" label="动作" min-width="210" />
            <el-table-column label="资源" min-width="150"><template #default="{ row }">{{ row.resource_type }} #{{ row.resource_id }}</template></el-table-column>
            <el-table-column label="结果" width="100"><template #default="{ row }"><el-tag :type="resultType(row.result)">{{ row.result }}</el-tag></template></el-table-column>
            <el-table-column prop="request_id" label="请求ID" min-width="240" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="成员邀请" name="invitations" :disabled="!canManageOrganization">
          <div class="pane-actions"><p>通过一次性邀请链接加入组织；列表只显示脱敏邮箱。</p><el-button type="primary" @click="invitationDialog=true; invitationLink=''">邀请成员</el-button></div>
          <el-alert v-if="invitationLink" type="success" :closable="false" title="邀请链接仅显示一次"><template #default><el-input :model-value="invitationLink" readonly /></template></el-alert>
          <el-table :data="invitations"><el-table-column prop="email_masked" label="邮箱" min-width="180"/><el-table-column prop="organization_role" label="组织角色" width="120"/><el-table-column prop="status" label="状态" width="110"/><el-table-column prop="expires_at" label="过期时间" min-width="180"/><el-table-column label="操作" width="150"><template #default="{row}"><el-button v-if="row.status==='PENDING'" link @click="actInvitation(row,'resend')">重发</el-button><el-button v-if="row.status==='PENDING'" link type="danger" @click="actInvitation(row,'revoke')">撤销</el-button></template></el-table-column></el-table>
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-dialog v-model="organizationDialog" title="新建组织" width="440px"><el-form label-position="top"><el-form-item label="组织名称"><el-input v-model="form.name" maxlength="100" /></el-form-item></el-form><template #footer><el-button @click="organizationDialog = false">取消</el-button><el-button type="primary" @click="submitOrganization">创建</el-button></template></el-dialog>
    <el-dialog v-model="workspaceDialog" title="新建工作空间" width="440px"><el-form label-position="top"><el-form-item label="工作空间名称"><el-input v-model="form.name" maxlength="100" /></el-form-item></el-form><template #footer><el-button @click="workspaceDialog = false">取消</el-button><el-button type="primary" @click="submitWorkspace">创建</el-button></template></el-dialog>
    <el-dialog v-model="memberDialog" :title="memberDialog === 'organization' ? '添加组织成员' : '添加空间成员'" width="440px"><el-form label-position="top"><el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item><el-form-item label="角色"><el-select v-if="memberDialog === 'organization'" v-model="form.organizationRole" style="width:100%"><el-option v-for="role in ['OWNER','ADMIN','MEMBER','AUDITOR']" :key="role" :value="role" :label="role" /></el-select><el-select v-else v-model="form.workspaceRole" style="width:100%"><el-option v-for="role in ['ADMIN','DEVELOPER','OPERATOR','VIEWER','AUDITOR']" :key="role" :value="role" :label="role" /></el-select></el-form-item></el-form><template #footer><el-button @click="memberDialog = null">取消</el-button><el-button type="primary" @click="submitMember">添加</el-button></template></el-dialog>
    <el-dialog v-model="invitationDialog" title="邀请成员" width="500px"><el-form label-position="top"><el-form-item label="受邀邮箱"><el-input v-model="form.email" type="email"/></el-form-item><el-form-item label="组织角色"><el-select v-model="form.organizationRole" style="width:100%"><el-option v-for="role in ['ADMIN','MEMBER','AUDITOR']" :key="role" :value="role" :label="role"/></el-select></el-form-item><el-form-item label="加入工作空间"><el-select v-model="selectedWorkspaceId" style="width:100%"><el-option v-for="item in workspaces" :key="item.id" :value="item.id" :label="item.name"/></el-select></el-form-item><el-form-item label="空间角色"><el-select v-model="form.workspaceRole" style="width:100%"><el-option v-for="role in ['ADMIN','DEVELOPER','OPERATOR','VIEWER','AUDITOR']" :key="role" :value="role" :label="role"/></el-select></el-form-item></el-form><template #footer><el-button @click="invitationDialog=false">取消</el-button><el-button type="primary" @click="submitInvitation();invitationDialog=false">创建邀请</el-button></template></el-dialog>
  </AppShell>
</template>

<style scoped>
.governance-summary{margin-bottom:22px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.governance-summary article{padding:18px;background:#fff;border:1px solid #e7ecf4;border-radius:15px}.governance-summary span,.governance-summary small{display:block;color:#8996aa;font-size:10px}.governance-summary strong{display:block;margin:10px 0 7px;color:#1d293d;font-size:20px}.governance-panel{min-height:420px;padding:22px;background:#fff;border:1px solid #e7ecf4;border-radius:17px}.pane-actions{min-height:48px;display:flex;align-items:flex-start;justify-content:space-between;gap:18px}.pane-actions p{margin:0;color:#8491a5;font-size:11px}@media(max-width:900px){.governance-summary{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.governance-summary{grid-template-columns:1fr}}
</style>
