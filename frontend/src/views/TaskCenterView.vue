<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppShell from '../components/AppShell.vue'
import { cancelGlobalTask, listGlobalTasks, retryGlobalTask, type GlobalTaskItem } from '../api'
import { errorMessage } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'
const loading=ref(false), tasks=ref<GlobalTaskItem[]>([]), total=ref(0), acting=ref<number|null>(null)
const filters=reactive({keyword:'',status:'',task_type:'',page:1,page_size:20})
const workspaceStore=useWorkspaceStore()
async function load(){loading.value=true;try{await workspaceStore.initialize();const data=await listGlobalTasks(filters);tasks.value=data.items;total.value=data.total}catch(e){ElMessage.error(errorMessage(e))}finally{loading.value=false}}
async function retryTask(row:GlobalTaskItem){acting.value=row.id;try{await retryGlobalTask(row.id);ElMessage.success('任务已重新进入队列');await load()}catch(e){ElMessage.error(errorMessage(e))}finally{acting.value=null}}
async function cancelTask(row:GlobalTaskItem){try{await ElMessageBox.confirm(`确认取消“${row.document_name}”的处理任务吗？`,'取消任务',{type:'warning'});acting.value=row.id;await cancelGlobalTask(row.id);await load()}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(errorMessage(e))}finally{acting.value=null}}
onMounted(load)
</script>
<template><AppShell eyebrow="OPERATIONS" title="任务中心" description="跨知识库跟踪文档处理进度，集中处理失败与停滞任务。"><template #actions><el-button @click="load">刷新</el-button></template>
<section class="task-panel"><div class="filters"><el-input v-model="filters.keyword" placeholder="文档或知识库" clearable @keyup.enter="load"/><el-select v-model="filters.status" placeholder="全部状态" clearable><el-option label="处理中" value="ACTIVE"/><el-option label="失败" value="FAILURE"/><el-option label="成功" value="SUCCESS"/><el-option label="已取消" value="CANCELLED"/></el-select><el-button type="primary" @click="filters.page=1;load()">查询</el-button></div>
<el-table v-loading="loading" :data="tasks"><el-table-column prop="document_name" label="文档" min-width="180"/><el-table-column prop="knowledge_base_name" label="知识库" min-width="150"/><el-table-column prop="task_type" label="类型" width="110"/><el-table-column label="进度" width="160"><template #default="{row}"><el-progress :percentage="row.progress" :status="row.status==='FAILURE'?'exception':row.status==='SUCCESS'?'success':undefined"/></template></el-table-column><el-table-column prop="current_stage" label="阶段" width="120"/><el-table-column prop="attempt_count" label="尝试" width="70"/><el-table-column prop="error_message" label="安全错误摘要" min-width="190" show-overflow-tooltip/><el-table-column label="操作" width="140"><template #default="{row}"><el-button v-if="['FAILURE','ENQUEUE_FAILED','CANCELLED'].includes(row.status)" link type="primary" :loading="acting===row.id" @click="retryTask(row)">重试</el-button><el-button v-if="['PENDING','PROCESSING','RETRYING','CANCEL_REQUESTED','ENQUEUE_FAILED'].includes(row.status)" link type="danger" :loading="acting===row.id" @click="cancelTask(row)">取消</el-button></template></el-table-column></el-table>
<el-pagination v-model:current-page="filters.page" :page-size="filters.page_size" :total="total" layout="prev,pager,next,total" @current-change="load"/></section></AppShell></template>
<style scoped>.task-panel{padding:20px;background:#fff;border:1px solid #e5edf8;border-radius:16px}.filters{display:grid;grid-template-columns:minmax(220px,1fr) 180px auto;gap:12px;margin-bottom:18px}.el-pagination{margin-top:18px;justify-content:flex-end}@media(max-width:700px){.filters{grid-template-columns:1fr}}</style>
