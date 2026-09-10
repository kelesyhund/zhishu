<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { login } from '../api'
import { errorMessage } from '../api/client'
import { useAuthStore } from '../stores/auth'
import BrandLogo from '../components/BrandLogo.vue'

const username = ref('demo')
const password = ref('demo123456')
const loading = ref(false)
const router = useRouter()
const auth = useAuthStore()

async function submit() {
  loading.value = true
  try {
    const data = await login(username.value, password.value)
    auth.setAuth(data.token, data.username)
    await router.push('/knowledge')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-visual">
      <div class="visual-orb orb-one" /><div class="visual-orb orb-two" />
      <BrandLogo inverse />
      <div class="visual-copy">
        <span>ENTERPRISE KNOWLEDGE INTELLIGENCE</span>
        <h1>让散落的企业知识，<br />成为可靠的生产力。</h1>
        <p>统一沉淀技术文档，借助混合检索与可信引用，为团队提供可追溯的智能问答体验。</p>
        <div class="feature-row">
          <div><strong>结构化解析</strong><small>父子切片与稳定证据</small></div>
          <div><strong>混合检索</strong><small>BM25 · Vector · Reranker</small></div>
          <div><strong>安全可控</strong><small>权限隔离与执行审计</small></div>
        </div>
      </div>
      <div class="knowledge-preview">
        <div class="preview-head"><span class="preview-logo">枢</span><span><strong>故障排查助手</strong><small>正在检索企业技术文档</small></span><i /></div>
        <div class="preview-question">Docker Compose 中环境变量为何没有生效？</div>
        <div class="preview-answer"><span /><p>已定位到配置优先级与插值规则相关章节，并找到 3 条可信依据。</p></div>
        <div class="preview-sources"><span>01 · Environment variables</span><span>02 · Compose interpolation</span></div>
      </div>
    </section>

    <section class="login-form-panel">
      <div class="login-card">
        <div class="mobile-brand"><BrandLogo /></div>
        <span class="login-eyebrow">欢迎使用知枢</span>
        <h2>登录企业知识工作台</h2>
        <p class="login-subtitle">管理知识、模型与智能应用，从一个可靠入口开始。</p>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input v-model="username" size="large" placeholder="请输入用户名" @keyup.enter="submit" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="password" type="password" size="large" placeholder="请输入密码" show-password @keyup.enter="submit" />
          </el-form-item>
          <el-button type="primary" size="large" :loading="loading" class="full-button" @click="submit">
            进入工作台
          </el-button>
        </el-form>
        <p class="register-link">还没有账号？<router-link to="/register">免费创建个人账号</router-link></p>
        <p class="demo-tip"><span>演示环境</span>账号已预填，可直接进入体验</p>
      </div>
      <p class="login-footer">知枢 · 企业知识中枢</p>
    </section>
  </main>
</template>

<style scoped>
.login-page{min-height:100vh;display:grid;grid-template-columns:minmax(520px,1.2fr) minmax(430px,.8fr);padding:0;background:#f8faff}.login-visual{position:relative;min-height:100vh;padding:42px clamp(42px,6vw,88px);display:flex;flex-direction:column;overflow:hidden;color:#fff;background:linear-gradient(145deg,#0b3f9d 0%,#1459cc 46%,#2d7bf0 100%)}.login-visual::before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);background-size:52px 52px;mask-image:linear-gradient(to bottom,#000,transparent 78%)}.visual-orb{position:absolute;border:1px solid rgba(255,255,255,.13);border-radius:50%}.orb-one{width:560px;height:560px;right:-210px;top:-230px;box-shadow:0 0 0 75px rgba(255,255,255,.025),0 0 0 150px rgba(255,255,255,.018)}.orb-two{width:300px;height:300px;left:-190px;bottom:-130px}.visual-copy{position:relative;z-index:1;max-width:680px;margin:auto 0 42px}.visual-copy>span{font-size:10px;font-weight:800;letter-spacing:.22em;color:#a9cdff}.visual-copy h1{margin:18px 0 22px;font-size:clamp(36px,4vw,58px);line-height:1.2;letter-spacing:-.04em}.visual-copy>p{max-width:600px;margin:0;color:rgba(255,255,255,.72);font-size:15px;line-height:1.9}.feature-row{margin-top:36px;display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.feature-row>div{padding:16px;border:1px solid rgba(255,255,255,.12);border-radius:13px;background:rgba(255,255,255,.07);backdrop-filter:blur(8px)}.feature-row strong,.feature-row small{display:block}.feature-row strong{font-size:13px}.feature-row small{margin-top:7px;color:rgba(255,255,255,.58);font-size:10px;line-height:1.5}.knowledge-preview{position:relative;z-index:1;width:min(590px,90%);margin:0 auto -165px;padding:17px;background:rgba(255,255,255,.96);border:1px solid rgba(255,255,255,.8);border-radius:20px 20px 0 0;box-shadow:0 -12px 55px rgba(0,26,80,.23);color:#293548}.preview-head{display:flex;align-items:center;gap:10px}.preview-logo{width:31px;height:31px;display:grid;place-items:center;color:#fff;background:#2563eb;border-radius:9px;font-size:12px;font-weight:800}.preview-head>span:nth-child(2){flex:1}.preview-head strong,.preview-head small{display:block}.preview-head strong{font-size:11px}.preview-head small{margin-top:4px;color:#8a98ad;font-size:8px}.preview-head i{width:7px;height:7px;background:#22c55e;border-radius:50%;box-shadow:0 0 0 4px #dcfce7}.preview-question{width:72%;margin:18px 0 13px auto;padding:11px 13px;color:#fff;background:#2563eb;border-radius:12px 4px 12px 12px;font-size:10px}.preview-answer{display:flex;gap:10px;padding:13px;background:#f5f8fc;border-radius:4px 12px 12px}.preview-answer span{flex:0 0 6px;height:6px;margin-top:4px;background:#3b82f6;border-radius:50%}.preview-answer p{margin:0;color:#526075;font-size:9px;line-height:1.65}.preview-sources{margin-top:10px;display:flex;gap:7px}.preview-sources span{padding:6px 8px;color:#55709a;background:#edf4ff;border-radius:6px;font-size:7px}.login-form-panel{min-height:100vh;padding:40px clamp(38px,6vw,78px);display:flex;flex-direction:column;justify-content:center;background:#fff}.login-card{width:100%;max-width:430px;margin:auto}.mobile-brand{display:none;margin-bottom:44px}.login-eyebrow{color:#2563eb;font-size:11px;font-weight:800;letter-spacing:.16em}.login-card h2{margin:13px 0 9px;color:#172033;font-size:28px;letter-spacing:-.025em}.login-subtitle{margin-bottom:32px;color:#8390a3;font-size:13px;line-height:1.7}.login-card :deep(.el-form-item__label){color:#42506a;font-size:12px;font-weight:650}.login-card :deep(.el-input__wrapper){min-height:46px;border-radius:10px;box-shadow:0 0 0 1px #dfe5ee inset}.login-card :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 1px #2563eb inset,0 0 0 3px #dbeafe}.full-button{height:46px;margin-top:8px;border-radius:10px;font-weight:700}.demo-tip{margin:18px 0 0;color:#98a3b4;font-size:11px;text-align:center}.demo-tip span{margin-right:7px;padding:3px 7px;color:#2563eb;background:#eff6ff;border-radius:12px}.login-footer{margin:30px 0 0;color:#a4adba;font-size:10px;text-align:center;letter-spacing:.08em}
.login-visual{background-image:linear-gradient(145deg,rgba(6,40,104,.94) 0%,rgba(15,78,176,.87) 48%,rgba(37,99,235,.72) 100%),url('../assets/zhishu-city-waterfront.jpg');background-size:cover;background-position:center 48%;background-repeat:no-repeat}.login-visual :deep(.brand-logo){position:relative;z-index:1}.login-visual::after{content:"";position:absolute;inset:auto 0 0;height:46%;background:linear-gradient(to top,rgba(4,30,82,.28),transparent);pointer-events:none}.visual-copy,.knowledge-preview{z-index:2}.visual-copy{margin:auto 0 26px}.knowledge-preview{width:min(590px,100%);margin:0;padding:15px 17px 17px;border-radius:18px;box-shadow:0 18px 55px rgba(0,26,80,.22)}
.register-link{margin:18px 0 0;color:#7b8798;font-size:12px;text-align:center}.register-link a{color:#2563eb;font-weight:700;text-decoration:none}.register-link a:hover{text-decoration:underline}
@media(max-width:960px){.login-page{grid-template-columns:1fr}.login-visual{display:none}.login-form-panel{padding:32px 24px}.mobile-brand{display:block}}
</style>
