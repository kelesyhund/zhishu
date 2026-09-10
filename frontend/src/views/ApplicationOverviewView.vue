<template>
  <AppShell
    v-loading="loading"
    eyebrow="RELEASE CENTER"
    :title="application?.name || '发布概览'"
    description="版本快照、公开访问、第三方凭证和运行记录集中在这里管理。"
  >
    <template #actions>
      <el-button @click="$router.push('/applications')">返回应用列表</el-button>
      <el-button @click="$router.push(`/applications/${applicationId}`)">编辑草稿</el-button>
      <el-button v-if="workspaceStore.can('application.operate') && application?.status === 'PUBLISHED'" type="warning" :loading="publishing" @click="disable">停用</el-button>
      <el-button v-if="workspaceStore.can('application.operate')" type="primary" :loading="publishing" @click="publish">发布新版本</el-button>
    </template>

    <section class="release-overview">
      <div><span>当前状态</span><strong>{{ statusText }}</strong><small>正式访问仅绑定已发布快照</small></div>
      <div><span>版本数量</span><strong>{{ versions.length }}</strong><small>保留历史版本并支持安全回滚</small></div>
      <div><span>访问凭证</span><strong>{{ credentials.length }}</strong><small>密钥只在创建时完整显示一次</small></div>
      <div><span>最近访问</span><strong>{{ logs.length }}</strong><small>记录来源、状态与端到端耗时</small></div>
    </section>

    <el-row :gutter="18">
      <el-col :span="14">
        <el-card class="section">
          <template #header><div class="card-title"><strong>版本</strong><el-tag :type="application?.status === 'PUBLISHED' ? 'success' : 'info'">{{ statusText }}</el-tag></div></template>
          <el-table :data="versions" empty-text="尚未发布版本">
            <el-table-column label="版本" width="90"><template #default="scope">v{{ scope.row.version }}</template></el-table-column>
            <el-table-column prop="published_at" label="发布时间" min-width="180"><template #default="scope">{{ formatTime(scope.row.published_at) }}</template></el-table-column>
            <el-table-column label="当前" width="80"><template #default="scope"><el-tag v-if="scope.row.version === application?.current_published_version_number" size="small" type="success">当前</el-tag></template></el-table-column>
            <el-table-column label="操作" width="90"><template #default="scope"><el-button link type="primary" :disabled="scope.row.version === application?.current_published_version_number" @click="rollback(scope.row)">回滚</el-button></template></el-table-column>
          </el-table>
        </el-card>

        <el-card class="section">
          <template #header><strong>公开访问与 iframe</strong></template>
          <el-alert title="公开链接只绑定正式版本。Token 仅在首次生成或轮换时完整显示。" type="info" :closable="false" />
          <div class="operation-row">
            <el-tag :type="publicAccess.enabled ? 'success' : 'info'">{{ publicAccess.enabled ? '已开启' : '未开启' }}</el-tag>
            <span v-if="publicAccess.token_masked">{{ publicAccess.token_masked }}</span>
            <el-button v-if="!publicAccess.enabled" type="primary" @click="enablePublic">开启</el-button>
            <el-button v-else type="warning" @click="disablePublic">关闭</el-button>
            <el-button :disabled="application?.status !== 'PUBLISHED'" @click="rotatePublic">轮换 Token</el-button>
          </div>
          <div v-if="revealedPublicToken" class="secret-box">
            <strong>请立即复制，离开后无法再次查看</strong>
            <code>{{ publicShareUrl }}</code>
            <el-button size="small" @click="copy(publicShareUrl)">复制公开链接</el-button>
            <code>&lt;iframe src=&quot;{{ embedUrl }}&quot; width=&quot;420&quot; height=&quot;640&quot;&gt;&lt;/iframe&gt;</code>
            <el-button size="small" @click="copy(`<iframe src=&quot;${embedUrl}&quot; width=&quot;420&quot; height=&quot;640&quot;></iframe>`)" >复制 iframe</el-button>
          </div>
          <el-form label-position="top" class="origins">
            <el-form-item label="允许嵌入的站点 Origin（每行一个；留空仅允许同源）">
              <el-input v-model="originsText" type="textarea" :rows="4" placeholder="https://portal.example.com" />
            </el-form-item>
            <el-button :disabled="!publicAccess.enabled" @click="saveOrigins">保存白名单</el-button>
          </el-form>
        </el-card>

        <el-card class="section">
          <template #header><strong>第三方 API Key</strong></template>
          <div class="operation-row"><el-button type="primary" @click="createCredential">创建 API Key</el-button><span class="hint">密钥仅显示一次，不会保存在浏览器。</span></div>
          <el-table :data="credentials" empty-text="尚未创建 API Key">
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="api_key_masked" label="密钥" />
            <el-table-column label="状态" width="90"><template #default="scope"><el-tag :type="scope.row.enabled ? 'success' : 'info'">{{ scope.row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" width="160"><template #default="scope"><el-button link @click="toggleCredential(scope.row)">{{ scope.row.enabled ? '停用' : '启用' }}</el-button><el-button link type="danger" @click="removeCredential(scope.row)">删除</el-button></template></el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card class="section">
          <template #header><strong>最近访问日志</strong></template>
          <el-table :data="logs" size="small" empty-text="暂无访问记录">
            <el-table-column prop="access_type" label="来源" width="90" />
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column prop="total_latency_ms" label="耗时(ms)" width="100" />
            <el-table-column prop="created_at" label="时间"><template #default="scope">{{ formatTime(scope.row.created_at) }}</template></el-table-column>
          </el-table>
        </el-card>
        <el-card class="section">
          <template #header><strong>API 调用示例</strong></template>
          <pre>POST /api/v1/applications/{{ applicationId }}/chat/completions
Authorization: Bearer &lt;仅显示一次的 API Key&gt;
Content-Type: application/json

{"messages":[{"role":"user","content":"你好"}],"stream":false}</pre>
        </el-card>
      </el-col>
    </el-row>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppShell from '../components/AppShell.vue'
import { useWorkspaceStore } from '../stores/workspace'
import {
  createApplicationCredential, deleteApplicationCredential, disableApplication,
  disableApplicationPublicAccess, enableApplicationPublicAccess, getApplication,
  getApplicationPublicAccess, listApplicationAccessLogs, listApplicationCredentials,
  listApplicationVersions, publishApplication, rollbackApplication, rotateApplicationPublicAccess,
  updateApplicationCredential, updateApplicationFrameOrigins,
  type ApplicationAccessLogItem, type ApplicationCredentialItem, type ApplicationItem,
  type ApplicationPublicAccess, type ApplicationVersionItem,
} from '../api'

const route = useRoute()
const workspaceStore = useWorkspaceStore()
const applicationId = Number(route.params.id)
const loading = ref(true), publishing = ref(false)
const application = ref<ApplicationItem | null>(null)
const versions = ref<ApplicationVersionItem[]>([]), credentials = ref<ApplicationCredentialItem[]>([])
const logs = ref<ApplicationAccessLogItem[]>([])
const publicAccess = ref<ApplicationPublicAccess>({ enabled: false })
const originsText = ref(''), revealedPublicToken = ref('')
const statusText = computed(() => ({ DRAFT:'草稿', PUBLISHED:'已发布', DISABLED:'已停用' }[application.value?.status || 'DRAFT']))
const publicShareUrl = computed(() => `${window.location.origin}/share/${encodeURIComponent(revealedPublicToken.value)}`)
const embedUrl = computed(() => `${window.location.origin}/api/public/applications/${encodeURIComponent(revealedPublicToken.value)}/embed/`)

function messageOf(error: unknown) { const value=error as {response?:{data?:{message?:string}};message?:string}; return value.response?.data?.message||value.message||'操作失败' }
function formatTime(value: string | null) { return value ? new Date(value).toLocaleString() : '—' }
async function load() {
  loading.value=true
  try {
    const [app, versionItems, access, credentialItems, logItems] = await Promise.all([
      getApplication(applicationId), listApplicationVersions(applicationId), getApplicationPublicAccess(applicationId),
      listApplicationCredentials(applicationId), listApplicationAccessLogs(applicationId),
    ])
    application.value=app; versions.value=versionItems; publicAccess.value=access; credentials.value=credentialItems; logs.value=logItems
    originsText.value=(access.allowed_frame_origins||[]).join('\n')
  } catch(error) { ElMessage.error(messageOf(error)) } finally { loading.value=false }
}
async function publish() { publishing.value=true; try { await publishApplication(applicationId); ElMessage.success('新版本已发布'); await load() } catch(error) { ElMessage.error(messageOf(error)) } finally { publishing.value=false } }
async function disable() { try { await ElMessageBox.confirm('停用后公开链接和 API 将立即不可用，历史数据保留。','停用应用',{type:'warning'}); await disableApplication(applicationId); ElMessage.success('应用已停用'); await load() } catch(error) { if(error!=='cancel'&&error!=='close')ElMessage.error(messageOf(error)) } }
async function rollback(item: ApplicationVersionItem) { try { await ElMessageBox.confirm(`将正式版本切换到 v${item.version}？草稿不会被覆盖。`,'版本回滚'); await rollbackApplication(applicationId,item.id); await load(); ElMessage.success('回滚成功') } catch(error) { if(error!=='cancel'&&error!=='close')ElMessage.error(messageOf(error)) } }
function acceptToken(value: ApplicationPublicAccess) { publicAccess.value=value; if(value.public_token)revealedPublicToken.value=value.public_token }
async function enablePublic() { try { acceptToken(await enableApplicationPublicAccess(applicationId)); ElMessage.success('公开访问已开启') } catch(error) { ElMessage.error(messageOf(error)) } }
async function rotatePublic() { try { await ElMessageBox.confirm('轮换后旧公开链接立即失效。','轮换 Token',{type:'warning'}); acceptToken(await rotateApplicationPublicAccess(applicationId)) } catch(error) { if(error!=='cancel'&&error!=='close')ElMessage.error(messageOf(error)) } }
async function disablePublic() { try { publicAccess.value=await disableApplicationPublicAccess(applicationId); revealedPublicToken.value='' } catch(error) { ElMessage.error(messageOf(error)) } }
async function saveOrigins() { try { const origins=originsText.value.split('\n').map(v=>v.trim()).filter(Boolean); publicAccess.value=await updateApplicationFrameOrigins(applicationId,origins); ElMessage.success('嵌入白名单已保存') } catch(error) { ElMessage.error(messageOf(error)) } }
async function createCredential() { try { const result=await ElMessageBox.prompt('输入便于识别的名称，例如“生产网站”','创建 API Key',{inputPattern:/\S+/,inputErrorMessage:'名称不能为空'}); const item=await createApplicationCredential(applicationId,result.value.trim()); credentials.value.unshift(item); await ElMessageBox.alert(item.api_key||'','密钥仅显示一次，请立即复制',{confirmButtonText:'我已保存'}) } catch(error) { if(error!=='cancel'&&error!=='close')ElMessage.error(messageOf(error)) } }
async function toggleCredential(item: ApplicationCredentialItem) { try { const updated=await updateApplicationCredential(applicationId,item.id,!item.enabled); Object.assign(item,updated) } catch(error) { ElMessage.error(messageOf(error)) } }
async function removeCredential(item: ApplicationCredentialItem) { try { await ElMessageBox.confirm(`删除凭证“${item.name}”？使用它的客户端会立即失效。`,'删除凭证',{type:'warning'}); await deleteApplicationCredential(applicationId,item.id); credentials.value=credentials.value.filter(v=>v.id!==item.id) } catch(error) { if(error!=='cancel'&&error!=='close')ElMessage.error(messageOf(error)) } }
async function copy(value: string) { try { await navigator.clipboard.writeText(value); ElMessage.success('已复制') } catch { ElMessage.error('复制失败，请手动选择') } }
onMounted(load)
</script>

<style scoped>
.release-overview{margin-bottom:20px;display:grid;grid-template-columns:repeat(4,1fr);gap:13px}.release-overview>div{padding:17px 18px;background:#fff;border:1px solid #e6ebf3;border-radius:14px}.release-overview span,.release-overview small{display:block}.release-overview span{color:#8b98aa;font-size:9px;font-weight:700;letter-spacing:.08em}.release-overview strong{display:block;margin:9px 0 6px;color:#263349;font-size:18px}.release-overview small{color:#a0aabc;font-size:8px;line-height:1.5}.card-title,.operation-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.section{margin-bottom:18px}.operation-row{justify-content:flex-start;margin:16px 0}.hint{color:#64748b}.secret-box{display:flex;flex-direction:column;gap:10px;margin:14px 0;padding:14px;background:#fff8ed;border:1px solid #f5c77d;border-radius:10px}.secret-box code,pre{white-space:pre-wrap;word-break:break-all;background:#f4f7fb;padding:10px;border-radius:8px}.origins{margin-top:18px}@media(max-width:1100px){.release-overview{grid-template-columns:repeat(2,1fr)}}@media(max-width:960px){.el-col{max-width:100%;flex:0 0 100%}}@media(max-width:600px){.release-overview{grid-template-columns:1fr}}
</style>
