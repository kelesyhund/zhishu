<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppShell from '../components/AppShell.vue'
import { getDashboardActivity, getDashboardAttention, getDashboardOverview, type AttentionItem, type AuditEventItem, type DashboardOverview } from '../api'
import { errorMessage } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'

const loading = ref(false)
const range = ref<'7d' | '30d'>('7d')
const overview = ref<DashboardOverview | null>(null)
const attention = ref<AttentionItem[]>([])
const activity = ref<AuditEventItem[]>([])
const workspaceStore = useWorkspaceStore()
async function load() {
  loading.value = true
  try {
    await workspaceStore.initialize()
    const [summary, todo, events] = await Promise.all([getDashboardOverview(range.value), getDashboardAttention(), getDashboardActivity()])
    overview.value = summary; attention.value = todo.items; activity.value = events.items
  } catch (error) { ElMessage.error(errorMessage(error)) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <AppShell eyebrow="ENTERPRISE OVERVIEW" title="企业工作台" description="掌握知识资产、智能应用与处理任务的真实运行状态。">
    <template #actions><el-radio-group v-model="range" @change="load"><el-radio-button value="7d">近7天</el-radio-button><el-radio-button value="30d">近30天</el-radio-button></el-radio-group></template>
    <div v-loading="loading">
      <section class="metric-grid">
        <article><span>知识库</span><strong>{{ overview?.counts.knowledge_bases || 0 }}</strong><small>当前工作空间</small></article>
        <article><span>文档资产</span><strong>{{ overview?.counts.documents || 0 }}</strong><small>{{ overview?.counts.documents_success || 0 }} 份可用</small></article>
        <article><span>已发布应用</span><strong>{{ overview?.counts.published_applications || 0 }}</strong><small>面向业务服务</small></article>
        <article><span>组织成员</span><strong>{{ overview?.counts.organization_members || 0 }}</strong><small>组织协作账号</small></article>
        <article><span>应用成功率</span><strong>{{ overview?.application_calls.success_rate || 0 }}%</strong><small>{{ overview?.application_calls.success || 0 }}/{{ overview?.application_calls.total || 0 }} 次</small></article>
        <article><span>P95响应</span><strong>{{ overview?.application_calls.p95_latency_ms || 0 }} ms</strong><small>完整请求耗时</small></article>
      </section>
      <section class="dashboard-grid">
        <article class="panel"><header><div><span>OPERATING TREND</span><h2>运行趋势</h2></div></header>
          <el-empty v-if="!overview?.trend.length" description="所选周期暂无调用数据" :image-size="72" />
          <div v-else class="trend"><div v-for="item in overview.trend" :key="item.date"><span>{{ item.date }}</span><b>{{ item.success }} / {{ item.total }}</b><el-progress :percentage="item.total ? Math.round(item.success * 100 / item.total) : 0" :show-text="false" /></div></div>
        </article>
        <article class="panel"><header><div><span>ATTENTION</span><h2>待处理事项</h2></div><el-tag v-if="attention.length" type="warning">{{ attention.length }}</el-tag></header>
          <el-empty v-if="!attention.length" description="当前没有待处理事项" :image-size="72" />
          <div v-else class="list"><div v-for="item in attention" :key="`${item.type}-${item.resource_id}`"><b>{{ item.title }}</b><p>{{ item.description }}</p></div></div>
        </article>
        <article class="panel"><header><div><span>RECENT ACTIVITY</span><h2>最近活动</h2></div></header>
          <el-empty v-if="!activity.length" description="当前角色暂无可见活动" :image-size="72" />
          <div v-else class="list"><div v-for="item in activity" :key="item.id"><b>{{ item.actor_username || '系统' }} · {{ item.action }}</b><p>{{ new Date(item.created_at).toLocaleString() }} · {{ item.result }}</p></div></div>
        </article>
      </section>
    </div>
  </AppShell>
</template>
<style scoped>
.metric-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.metric-grid article,.panel{padding:22px;background:#fff;border:1px solid #e5edf8;border-radius:16px;box-shadow:0 9px 30px rgba(30,64,175,.045)}.metric-grid span,.panel header span{color:#7890b4;font-size:11px;font-weight:700;letter-spacing:.08em}.metric-grid strong{display:block;margin:10px 0 7px;color:#172033;font-size:28px}.metric-grid small{color:#93a0b2}.dashboard-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.panel header{display:flex;justify-content:space-between}.panel h2{margin:7px 0 18px;font-size:18px}.list,.trend{display:grid;gap:10px;max-height:370px;overflow:auto}.list>div,.trend>div{padding:13px 14px;background:#f7faff;border-radius:10px}.list b{font-size:12px}.list p{margin:5px 0 0;color:#7c899b;font-size:11px}.trend>div{display:grid;grid-template-columns:90px 60px 1fr;align-items:center;gap:10px;font-size:11px}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.dashboard-grid{grid-template-columns:1fr}}@media(max-width:560px){.metric-grid{grid-template-columns:1fr}}
</style>
