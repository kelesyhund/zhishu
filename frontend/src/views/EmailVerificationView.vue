<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrandLogo from '../components/BrandLogo.vue'
import { confirmEmailVerification } from '../api'
const route=useRoute(),router=useRouter(),state=ref<'loading'|'success'|'error'>('loading')
onMounted(async()=>{try{await confirmEmailVerification(String(route.query.token||''));state.value='success'}catch{state.value='error'}})
</script>
<template><main class="verify"><section><BrandLogo/><h1>{{state==='loading'?'正在验证邮箱':state==='success'?'邮箱验证成功':'验证链接无效或已过期'}}</h1><p>{{state==='success'?'你的邮箱身份已确认，可以安全接收账号通知。':'请等待验证结果，或返回账号安全页重新发送。'}}</p><el-button type="primary" @click="router.replace(state==='success'?'/dashboard':'/login')">返回知枢</el-button></section></main></template>
<style scoped>.verify{min-height:100vh;display:grid;place-items:center;background:#f4f8ff}.verify section{width:min(430px,90%);padding:34px;text-align:center;background:#fff;border:1px solid #dce7f6;border-radius:18px}.verify h1{margin-top:28px}.verify p{color:#7b8798;line-height:1.8}.verify .el-button{margin-top:12px}</style>
