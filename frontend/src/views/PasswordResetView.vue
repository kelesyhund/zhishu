<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import BrandLogo from '../components/BrandLogo.vue'
import { confirmPasswordReset } from '../api'
import { errorMessage } from '../api/client'
const route=useRoute(),router=useRouter(),loading=ref(false),form=reactive({password:'',confirm:''})
async function submit(){if(form.password!==form.confirm)return ElMessage.warning('两次密码不一致');loading.value=true;try{await confirmPasswordReset(String(route.query.uid||''),String(route.query.token||''),form.password);form.password=form.confirm='';ElMessage.success('密码已重置');await router.replace('/login')}catch(e){ElMessage.error(errorMessage(e))}finally{loading.value=false}}
</script>
<template><main class="reset"><section><BrandLogo/><h1>重置密码</h1><p>设置一个新的高强度密码，完成后旧登录会话将全部失效。</p><el-input v-model="form.password" type="password" show-password placeholder="新密码"/><el-input v-model="form.confirm" type="password" show-password placeholder="确认新密码"/><el-button type="primary" :loading="loading" @click="submit">确认重置</el-button></section></main></template>
<style scoped>.reset{min-height:100vh;display:grid;place-items:center;background:#f4f8ff}.reset section{width:min(420px,90%);padding:32px;background:#fff;border:1px solid #dce7f6;border-radius:18px}.reset p{color:#7b8798;line-height:1.7}.reset .el-input{margin:7px 0}.reset .el-button{width:100%;margin-top:12px}</style>
