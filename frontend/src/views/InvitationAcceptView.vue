<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import BrandLogo from '../components/BrandLogo.vue'
import { acceptInvitation, getCurrentUser, previewInvitation, registerAndAcceptInvitation, type InvitationPreview } from '../api'
import { errorMessage } from '../api/client'
import { useAuthStore } from '../stores/auth'
const route=useRoute(),router=useRouter(),auth=useAuthStore(),token=String(route.params.token||''),preview=ref<InvitationPreview|null>(null),loading=ref(true),submitting=ref(false)
const form=reactive({username:'',password:'',password_confirm:''})
async function join(){submitting.value=true;try{if(auth.loggedIn){await acceptInvitation(token)}else{await registerAndAcceptInvitation(token,form);auth.setAuth(await getCurrentUser())}history.replaceState(null,'','/dashboard');ElMessage.success('已成功加入组织');await router.replace('/dashboard')}catch(e){ElMessage.error(errorMessage(e))}finally{submitting.value=false}}
onMounted(async()=>{try{await auth.initialize();preview.value=await previewInvitation(token)}catch(e){ElMessage.error(errorMessage(e))}finally{loading.value=false}})
</script>
<template><main class="invite-page"><section class="invite-card" v-loading="loading"><BrandLogo/><template v-if="preview"><span class="eyebrow">ORGANIZATION INVITATION</span><h1>加入 {{ preview.organization_name }}</h1><p>邀请账号：{{ preview.email_masked }}<br/>有效期至：{{ new Date(preview.expires_at).toLocaleString() }}</p><el-form v-if="!auth.loggedIn" label-position="top"><el-form-item label="用户名"><el-input v-model="form.username"/></el-form-item><el-form-item label="密码"><el-input v-model="form.password" type="password" show-password/></el-form-item><el-form-item label="确认密码"><el-input v-model="form.password_confirm" type="password" show-password/></el-form-item></el-form><el-button type="primary" size="large" :loading="submitting" @click="join">{{auth.loggedIn?'接受邀请':'注册并接受邀请'}}</el-button></template></section></main></template>
<style scoped>.invite-page{min-height:100vh;display:grid;place-items:center;padding:24px;background:linear-gradient(145deg,#edf5ff,#f8fbff)}.invite-card{width:min(460px,100%);padding:32px;background:#fff;border:1px solid #dce7f6;border-radius:20px;box-shadow:0 20px 55px rgba(37,99,235,.12)}.eyebrow{display:block;margin-top:30px;color:#2563eb;font-size:10px;font-weight:800;letter-spacing:.16em}.invite-card h1{margin:10px 0;color:#172033}.invite-card p{color:#758399;line-height:1.8}.invite-card .el-button{width:100%;margin-top:8px}</style>
