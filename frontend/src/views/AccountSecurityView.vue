<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppShell from '../components/AppShell.vue'
import { changePassword, listAccountSessions, logoutOtherSessions, requestEmailVerification, revokeAccountSession, type AccountSessionItem } from '../api'
import { errorMessage } from '../api/client'
import { useAuthStore } from '../stores/auth'
const auth=useAuthStore(), sessions=ref<AccountSessionItem[]>([]), loading=ref(false), changing=ref(false)
const form=reactive({current:'',password:'',confirm:''})
async function load(){loading.value=true;try{sessions.value=(await listAccountSessions()).items}catch(e){ElMessage.error(errorMessage(e))}finally{loading.value=false}}
async function submit(){if(form.password!==form.confirm)return ElMessage.warning('两次输入的新密码不一致');changing.value=true;try{await changePassword(form.current,form.password);form.current=form.password=form.confirm='';ElMessage.success('密码已修改，其他设备已退出');await load()}catch(e){ElMessage.error(errorMessage(e))}finally{changing.value=false}}
async function revoke(id:string){try{await revokeAccountSession(id);ElMessage.success('登录会话已撤销');await load()}catch(e){ElMessage.error(errorMessage(e))}}
async function revokeOthers(){try{const count=await logoutOtherSessions();ElMessage.success(`已退出 ${count} 个其他会话`);await load()}catch(e){ElMessage.error(errorMessage(e))}}
async function verifyEmail(){try{await requestEmailVerification();ElMessage.success('验证邮件已发送')}catch(e){ElMessage.error(errorMessage(e))}}
onMounted(load)
</script>
<template><AppShell eyebrow="ACCOUNT SECURITY" title="账号安全" description="管理密码、邮箱身份与当前账号的登录设备。"><template #actions><el-button @click="revokeOthers">退出其他设备</el-button></template>
<section class="security-grid"><article class="panel"><h2>身份状态</h2><div class="identity"><span>{{ auth.currentUser?.email_masked || '未配置邮箱' }}</span><el-tag :type="auth.currentUser?.email_verified?'success':'warning'">{{ auth.currentUser?.email_verified?'已验证':'待验证' }}</el-tag></div><p>邮箱用于成员邀请与安全找回密码，系统不会在页面返回完整邮箱。</p><el-button v-if="auth.currentUser?.email_configured&&!auth.currentUser?.email_verified" @click="verifyEmail">发送验证邮件</el-button></article>
<article class="panel"><h2>修改密码</h2><el-form label-position="top"><el-form-item label="当前密码"><el-input v-model="form.current" type="password" show-password/></el-form-item><el-form-item label="新密码"><el-input v-model="form.password" type="password" show-password/></el-form-item><el-form-item label="确认新密码"><el-input v-model="form.confirm" type="password" show-password/></el-form-item><el-button type="primary" :loading="changing" @click="submit">更新密码</el-button></el-form></article></section>
<section class="panel sessions"><header><h2>登录设备</h2><el-button @click="load">刷新</el-button></header><el-table v-loading="loading" :data="sessions"><el-table-column prop="user_agent" label="设备" min-width="260"/><el-table-column prop="ip_summary" label="网络摘要" width="130"/><el-table-column prop="last_seen_at" label="最近活跃" min-width="180"/><el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.current?'success':'info'">{{row.current?'当前':'其他'}}</el-tag></template></el-table-column><el-table-column label="操作" width="100"><template #default="{row}"><el-button v-if="!row.current" link type="danger" @click="revoke(row.id)">撤销</el-button></template></el-table-column></el-table></section></AppShell></template>
<style scoped>.security-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{padding:22px;background:#fff;border:1px solid #e5edf8;border-radius:16px}.panel h2{margin:0 0 18px;font-size:17px}.panel p{color:#7c899b;font-size:12px;line-height:1.8}.identity{display:flex;align-items:center;justify-content:space-between;padding:15px;background:#f7faff;border-radius:10px}.sessions{margin-top:18px}.sessions header{display:flex;justify-content:space-between}@media(max-width:800px){.security-grid{grid-template-columns:1fr}}</style>
