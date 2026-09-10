<template>
  <div class="public-shell">
    <main class="chat-card" v-loading="loading">
      <header>
        <div class="public-profile"><BrandLogo compact /><div><span>由知枢提供知识服务</span><h1>{{ profile?.name || 'AI 知识应用' }}</h1><p>{{ profile?.description }}</p></div></div>
        <el-button text :disabled="sending" @click="newConversation">新对话</el-button>
      </header>
      <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
      <section v-else class="messages">
        <div v-if="messages.length === 0" class="welcome">
          <p>{{ profile?.welcome_message || '你好，请输入你想了解的问题。' }}</p>
          <div class="suggestions"><el-button v-for="item in profile?.suggested_questions" :key="item" plain @click="ask(item)">{{ item }}</el-button></div>
        </div>
        <article v-for="(item,index) in messages" :key="index" :class="['message',item.role]">
          <div class="content">{{ item.content }}</div>
          <details v-if="item.role === 'assistant' && item.references.length && profile?.show_references">
            <summary>查看 {{ item.references.length }} 条引用</summary>
            <div v-for="(reference,referenceIndex) in item.references" :key="referenceIndex" class="reference">
              <strong>{{ reference.knowledge_base_name ? `${reference.knowledge_base_name} / ` : '' }}{{ reference.document_name }}</strong>
              <p>{{ reference.content }}</p>
            </div>
          </details>
        </article>
      </section>
      <footer v-if="!loadError">
        <el-input v-model="question" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="输入问题，Ctrl+Enter 发送" @keydown.ctrl.enter="send" />
        <el-button type="primary" :loading="sending" :disabled="!question.trim()" @click="send">发送</el-button>
      </footer>
      <div class="brand">由 知枢 企业知识中枢 提供支持 · v{{ profile?.version || '—' }}</div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import BrandLogo from '../components/BrandLogo.vue'
import {
  createPublicVisitor, getPublicApplicationProfile, listPublicApplicationMessages,
  streamPublicApplicationChat, type MessageItem, type PublicApplicationProfile, type ReferenceItem,
} from '../api'

const route=useRoute(), publicToken=String(route.params.token||'')
const tokenPrefix=publicToken.split('_').slice(0,3).join('_') || 'unknown'
const visitorStorageKey=`kc_public_visitor_${tokenPrefix}`, conversationStorageKey=`kc_public_conversation_${tokenPrefix}`
const profile=ref<PublicApplicationProfile|null>(null), loading=ref(true), loadError=ref(''), sending=ref(false), question=ref('')
const visitorToken=ref(''), conversationId=ref<number|null>(null)
const messages=ref<Array<{role:'user'|'assistant';content:string;references:ReferenceItem[]}>>([])

function messageOf(error: unknown) { const value=error as {response?:{data?:{message?:string}};message?:string}; return value.response?.data?.message||value.message||'请求失败' }
async function initialize() {
  loading.value=true
  try {
    profile.value=await getPublicApplicationProfile(publicToken)
    visitorToken.value=sessionStorage.getItem(visitorStorageKey)||''
    if(!visitorToken.value){ visitorToken.value=await createPublicVisitor(publicToken); sessionStorage.setItem(visitorStorageKey,visitorToken.value) }
    const saved=Number(sessionStorage.getItem(conversationStorageKey)||0)
    if(saved>0){
      try { const page=await listPublicApplicationMessages(publicToken,visitorToken.value,saved); conversationId.value=saved; messages.value=page.items.map((item:MessageItem)=>({role:item.role,content:item.content,references:item.references||[]})) }
      catch { sessionStorage.removeItem(conversationStorageKey); conversationId.value=null }
    }
  } catch(error) { loadError.value=messageOf(error) } finally { loading.value=false }
}
function newConversation(){ conversationId.value=null; messages.value=[]; sessionStorage.removeItem(conversationStorageKey) }
function ask(value:string){ question.value=value; void send() }
async function send(){
  const text=question.value.trim(); if(!text||sending.value||!visitorToken.value)return
  messages.value.push({role:'user',content:text,references:[]},{role:'assistant',content:'',references:[]}); question.value=''; sending.value=true
  const assistant=messages.value[messages.value.length-1]
  try {
    await streamPublicApplicationChat(publicToken,visitorToken.value,text,conversationId.value,(event,data)=>{
      const value=data as {conversation_id?:number;content?:string;message?:string}|ReferenceItem[]
      if(event==='meta'&&!Array.isArray(value)&&typeof value.conversation_id==='number'){ conversationId.value=value.conversation_id; sessionStorage.setItem(conversationStorageKey,String(value.conversation_id)) }
      if(event==='content'&&!Array.isArray(value)&&value.content)assistant.content+=value.content
      if(event==='references'&&Array.isArray(value))assistant.references=value
      if(event==='error'&&!Array.isArray(value))throw new Error(value.message||'回答失败')
    })
  } catch(error){ assistant.content=assistant.content||`回答失败：${messageOf(error)}`; ElMessage.error(messageOf(error)) } finally { sending.value=false }
}
onMounted(initialize)
</script>

<style scoped>
.public-shell{min-height:100vh;padding:24px;background:radial-gradient(circle at 10% 10%,#dcecff,transparent 32%),linear-gradient(145deg,#f2f7ff,#f8fafc 58%,#edf5ff)}.chat-card{max-width:960px;min-height:calc(100vh - 48px);margin:0 auto;display:flex;flex-direction:column;overflow:hidden;background:#fff;border:1px solid rgba(255,255,255,.85);border-radius:20px;box-shadow:0 22px 65px rgba(32,72,135,.14)}.chat-card header{padding:20px 25px;display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,.94);border-bottom:1px solid #e7edf5}.public-profile{display:flex;align-items:center;gap:13px}.public-profile>div>span{color:#2563eb;font-size:8px;font-weight:800;letter-spacing:.12em}.chat-card h1{margin:4px 0 4px;color:#253147;font-size:18px}.chat-card header p{margin:0;color:#8693a5;font-size:10px}.messages{flex:1;overflow:auto;padding:26px;background-image:radial-gradient(circle at 50% 0,rgba(219,234,254,.34),transparent 32%)}.welcome{max-width:620px;margin:auto;text-align:center;color:#5a687c;padding:90px 20px}.suggestions{display:flex;flex-wrap:wrap;justify-content:center;gap:10px}.message{max-width:78%;margin:13px 0;padding:12px 14px;border-radius:14px;white-space:pre-wrap}.message.user{margin-left:auto;color:#fff;background:linear-gradient(145deg,#2563eb,#3378e6);box-shadow:0 7px 18px rgba(37,99,235,.12)}.message.assistant{color:#435168;background:#f2f5f9;border:1px solid #e7ecf3}.message details{margin-top:10px;padding-top:8px;border-top:1px solid #d7e0ec;font-size:13px}.reference{margin-top:8px}.reference p{margin:3px 0;color:#5d6b7e}.chat-card footer{padding:15px 21px;display:flex;gap:12px;align-items:flex-end;background:#fff;border-top:1px solid #e7edf5}.chat-card footer .el-button{height:74px}.brand{padding:0 0 11px;color:#9ba7b8;text-align:center;font-size:10px}@media(max-width:600px){.public-shell{padding:0}.chat-card{min-height:100vh;border-radius:0}.message{max-width:92%}}
</style>
