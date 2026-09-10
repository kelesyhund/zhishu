<template>
  <AppShell
    v-loading="loading"
    eyebrow="APPLICATION BUILDER"
    :title="form.name || '应用配置'"
    description="编辑草稿不会影响正在使用的正式版本。"
  >
    <template #actions>
      <el-button @click="$router.push('/applications')">返回应用列表</el-button>
      <el-button @click="$router.push(`/applications/${applicationId}/overview`)">发布概览</el-button>
      <el-button v-if="workspaceStore.can('application.write')" type="primary" :loading="saving" @click="save">保存草稿</el-button>
    </template>

    <div class="builder-tip"><span>草稿工作区</span><p>左侧完成知识、模型与行为配置，右侧可以随时发起真实预览。</p></div>

    <div class="layout">
      <el-card class="settings">
        <el-form label-position="top">
          <el-divider content-position="left">基础信息</el-divider>
          <el-form-item label="应用名称"><el-input v-model="form.name" maxlength="100" /></el-form-item>
          <el-form-item label="应用说明"><el-input v-model="form.description" type="textarea" maxlength="1000" /></el-form-item>
          <el-form-item label="Chat模型">
            <el-select v-model="form.chat_model_config_id" clearable placeholder="系统默认/本地演示">
              <el-option v-for="item in chatModels" :key="item.id" :label="`${item.name} / ${item.model_name}`" :value="item.id" />
            </el-select>
          </el-form-item>

          <el-divider content-position="left">知识库</el-divider>
          <el-alert title="最多选择5个知识库；跨库结果按权重RRF融合" type="info" :closable="false" />
          <el-checkbox-group v-model="selectedKnowledgeIds" class="knowledge-options">
            <el-checkbox v-for="item in knowledgeOptions" :key="item.id" :value="item.id">{{ item.name }}</el-checkbox>
          </el-checkbox-group>

          <el-divider content-position="left">回答与展示</el-divider>
          <el-form-item label="系统Prompt"><el-input v-model="form.system_prompt" type="textarea" :rows="5" maxlength="4000" show-word-limit /></el-form-item>
          <el-form-item label="欢迎语"><el-input v-model="form.welcome_message" maxlength="500" /></el-form-item>
          <el-form-item label="建议问题（每行一条，最多6条）"><el-input v-model="suggestedText" type="textarea" :rows="4" /></el-form-item>
          <el-form-item><el-checkbox v-model="form.show_references">向用户展示知识引用</el-checkbox></el-form-item>
          <div class="row"><el-form-item label="全局Top-K"><el-input-number v-model="form.global_top_k" :min="1" :max="20" /></el-form-item><el-form-item label="上下文字符预算"><el-input-number v-model="form.max_context_chars" :min="1000" :max="30000" :step="500" /></el-form-item></div>

          <el-divider content-position="left">Agent</el-divider>
          <el-form-item><el-switch v-model="form.agent_enabled" active-text="启用Agent" /></el-form-item>
          <el-alert v-if="form.agent_enabled" title="当前应用Agent公开运行仅支持单知识库；多库发布会被后端拒绝" type="warning" :closable="false" />
          <el-form-item label="最大步骤"><el-input-number v-model="form.agent_max_steps" :min="1" :max="10" /></el-form-item>
          <el-form-item label="Agent Prompt"><el-input v-model="form.agent_system_prompt" type="textarea" :rows="4" maxlength="4000" /></el-form-item>
          <el-form-item label="允许工具"><el-checkbox-group v-model="form.enabled_tools"><el-checkbox value="knowledge_search">知识检索</el-checkbox><el-checkbox value="document_list">文档列表</el-checkbox><el-checkbox value="calculator">安全计算器</el-checkbox></el-checkbox-group></el-form-item>
        </el-form>
      </el-card>

      <el-card class="preview">
        <template #header><strong>草稿预览</strong></template>
        <div class="messages">
          <div v-if="messages.length === 0" class="empty">保存配置后，在这里测试应用回答</div>
          <div v-for="(item,index) in messages" :key="index" :class="['message', item.role]">{{ item.content }}</div>
        </div>
        <el-input v-model="question" type="textarea" :rows="3" placeholder="输入测试问题" @keydown.ctrl.enter="send" />
        <el-button class="send" type="primary" :loading="sending" @click="send">发送（Ctrl+Enter）</el-button>
      </el-card>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import AppShell from '../components/AppShell.vue'
import { useWorkspaceStore } from '../stores/workspace'
import {
  getApplication,
  listKnowledgeBases,
  listModelConfigs,
  streamApplicationPreview,
  updateApplication,
  updateApplicationKnowledgeBases,
  type KnowledgeBase,
  type ModelConfigItem,
} from '../api'

const route = useRoute()
const workspaceStore = useWorkspaceStore()
const applicationId = Number(route.params.id)
const loading = ref(true), saving = ref(false), sending = ref(false)
const knowledgeOptions = ref<KnowledgeBase[]>([]), chatModels = ref<ModelConfigItem[]>([])
const selectedKnowledgeIds = ref<number[]>([])
const suggestedText = ref('')
const question = ref('')
const conversationId = ref<number | null>(null)
const messages = ref<Array<{role:'user'|'assistant';content:string}>>([])
const form = reactive({ name:'', description:'', chat_model_config_id:null as number|null, system_prompt:'', welcome_message:'', show_references:true, agent_enabled:false, agent_max_steps:5, agent_system_prompt:'', enabled_tools:[] as string[], global_top_k:5, max_context_chars:6000 })

function messageOf(error: unknown) { const value=error as {response?:{data?:{message?:string}};message?:string}; return value.response?.data?.message||value.message||'操作失败' }
async function load() {
  loading.value = true
  try {
    const [application, knowledgePage, models] = await Promise.all([getApplication(applicationId), listKnowledgeBases({page_size:100}), listModelConfigs('CHAT',1,100)])
    Object.assign(form, application)
    selectedKnowledgeIds.value = application.knowledge_bases.filter(item=>item.enabled).map(item=>item.knowledge_base_id)
    suggestedText.value = application.suggested_questions.join('\n')
    knowledgeOptions.value = knowledgePage.items
    chatModels.value = models.items
  } catch(error) { ElMessage.error(messageOf(error)) } finally { loading.value=false }
}
async function save() {
  if (!form.name.trim()) return ElMessage.warning('请输入应用名称')
  if (selectedKnowledgeIds.value.length > 5) return ElMessage.warning('最多绑定5个知识库')
  saving.value=true
  try {
    await updateApplication(applicationId, {...form, suggested_questions:suggestedText.value.split('\n').map(v=>v.trim()).filter(Boolean).slice(0,6)})
    await updateApplicationKnowledgeBases(applicationId, selectedKnowledgeIds.value.map((id,index)=>({knowledge_base_id:id,position:index+1,weight:1,enabled:true})))
    ElMessage.success('草稿已保存')
  } catch(error) { ElMessage.error(messageOf(error)) } finally { saving.value=false }
}
async function send() {
  const text=question.value.trim(); if(!text||sending.value)return
  await save(); messages.value.push({role:'user',content:text}); messages.value.push({role:'assistant',content:''}); question.value=''; sending.value=true
  try {
    await streamApplicationPreview(applicationId,text,conversationId.value,(event,data)=>{
      const value=data as {conversation_id?:number;content?:string;message?:string}
      if(event==='meta'&&typeof value.conversation_id==='number')conversationId.value=value.conversation_id
      if(event==='content'&&value.content)messages.value[messages.value.length-1].content+=value.content
      if(event==='error')throw new Error(value.message||'回答失败')
    })
  } catch(error) { messages.value[messages.value.length-1].content=`失败：${messageOf(error)}` } finally { sending.value=false }
}
onMounted(load)
</script>

<style scoped>
.builder-tip{margin-bottom:17px;padding:13px 16px;display:flex;align-items:center;gap:12px;color:#6f7e92;background:linear-gradient(100deg,#edf5ff,#f8fbff);border:1px solid #dce9fa;border-radius:12px;font-size:11px}.builder-tip span{padding:4px 8px;color:#2563eb;background:#fff;border-radius:14px;font-size:9px;font-weight:800}.builder-tip p{margin:0}.layout{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(340px,.85fr);gap:18px}.settings,.preview{border-radius:16px}.settings :deep(.el-divider__text){color:#315caa;font-size:11px;font-weight:800;letter-spacing:.05em}.knowledge-options{display:flex;flex-direction:column;gap:8px;margin:14px 0}.knowledge-options :deep(.el-checkbox){height:auto;margin:0;padding:10px 12px;background:#f6f8fc;border-radius:9px}.row{display:flex;gap:28px}.preview{height:calc(100vh - 205px);position:sticky;top:20px}.messages{height:calc(100% - 160px);overflow:auto;margin-bottom:12px}.empty{color:#94a3b8;text-align:center;padding:70px 10px}.message{padding:10px 12px;border-radius:10px;margin:8px 0;white-space:pre-wrap}.message.user{color:#fff;background:#2563eb;margin-left:40px}.message.assistant{background:#f1f5f9;margin-right:40px}.send{width:100%;margin-top:10px}@media(max-width:980px){.layout{grid-template-columns:1fr}.preview{position:static;height:600px}}
</style>
