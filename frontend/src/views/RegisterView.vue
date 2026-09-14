<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'

import { getCurrentUser, register } from '../api'
import { errorMessage } from '../api/client'
import BrandLogo from '../components/BrandLogo.vue'
import { useAuthStore } from '../stores/auth'

interface RegisterForm {
  username: string
  email: string
  password: string
  passwordConfirm: string
}

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive<RegisterForm>({ username: '', email: '', password: '', passwordConfirm: '' })
const router = useRouter()
const auth = useAuthStore()

const validatePasswordConfirm = (_rule: unknown, value: string, callback: (error?: Error) => void) => {
  if (value !== form.password) callback(new Error('两次输入的密码不一致'))
  else callback()
}

const rules: FormRules<RegisterForm> = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 30, message: '用户名长度为 3～30 个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '请输入正确的邮箱地址', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, max: 128, message: '密码长度至少为 8 个字符', trigger: 'blur' },
  ],
  passwordConfirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validatePasswordConfirm, trigger: ['blur', 'change'] },
  ],
}

async function submit() {
  if (!formRef.value || loading.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await register({
      username: form.username.trim(),
      email: form.email.trim(),
      password: form.password,
      password_confirm: form.passwordConfirm,
    })
    form.password = ''
    form.passwordConfirm = ''
    auth.setAuth(await getCurrentUser())
    ElMessage.success('注册成功，已为你创建个人组织和默认工作空间')
    await router.push('/dashboard')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="register-page">
    <section class="register-visual">
      <BrandLogo inverse />
      <div class="visual-copy">
        <span>START WITH TRUSTED KNOWLEDGE</span>
        <h1>从一个工作空间开始，<br />构建企业知识中枢。</h1>
        <p>注册后系统将自动创建你的个人组织与默认工作空间。你可以随后邀请成员、上传文档并发布智能应用。</p>
        <ul>
          <li><b>01</b><span><strong>组织隔离</strong><small>知识资产按组织和工作空间安全隔离</small></span></li>
          <li><b>02</b><span><strong>可信问答</strong><small>混合检索、引用溯源与无答案拒答</small></span></li>
          <li><b>03</b><span><strong>协作治理</strong><small>成员角色、操作审计与模型配置管理</small></span></li>
        </ul>
      </div>
    </section>

    <section class="register-form-panel">
      <div class="register-card">
        <div class="mobile-brand"><BrandLogo /></div>
        <span class="eyebrow">创建知枢账号</span>
        <h2>开始搭建你的知识工作台</h2>
        <p class="subtitle">账号创建后将直接登录，无需重复输入密码。</p>
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" size="large" maxlength="30" placeholder="3～30 个字符" autocomplete="username" />
          </el-form-item>
          <el-form-item label="邮箱" prop="email">
            <el-input v-model="form.email" size="large" maxlength="254" placeholder="用于后续接收账号通知" autocomplete="email" />
          </el-form-item>
          <div class="password-grid">
            <el-form-item label="密码" prop="password">
              <el-input v-model="form.password" type="password" size="large" placeholder="至少 8 个字符" show-password autocomplete="new-password" />
            </el-form-item>
            <el-form-item label="确认密码" prop="passwordConfirm">
              <el-input v-model="form.passwordConfirm" type="password" size="large" placeholder="再次输入密码" show-password autocomplete="new-password" @keyup.enter="submit" />
            </el-form-item>
          </div>
          <el-button type="primary" size="large" :loading="loading" class="full-button" @click="submit">创建账号并进入工作台</el-button>
        </el-form>
        <p class="login-link">已有账号？<router-link to="/login">返回登录</router-link></p>
      </div>
      <p class="footer">注册即表示你同意按所在组织的安全规范使用知枢</p>
    </section>
  </main>
</template>

<style scoped>
.register-page{min-height:100vh;display:grid;grid-template-columns:minmax(500px,1fr) minmax(520px,1fr);background:#f8faff}.register-visual{position:relative;min-height:100vh;padding:42px clamp(42px,6vw,88px);display:flex;flex-direction:column;overflow:hidden;color:#fff;background-image:linear-gradient(145deg,rgba(6,40,104,.95),rgba(15,78,176,.88) 50%,rgba(37,99,235,.74)),url('../assets/zhishu-city-waterfront.jpg');background-size:cover;background-position:center}.register-visual::after{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);background-size:52px 52px;mask-image:linear-gradient(to bottom,#000,transparent 80%)}.register-visual :deep(.brand-logo),.visual-copy{position:relative;z-index:1}.visual-copy{max-width:650px;margin:auto 0}.visual-copy>span,.eyebrow{font-size:10px;font-weight:800;letter-spacing:.2em}.visual-copy>span{color:#b8d7ff}.visual-copy h1{margin:18px 0 22px;font-size:clamp(35px,3.8vw,55px);line-height:1.2;letter-spacing:-.04em}.visual-copy>p{max-width:570px;margin:0;color:rgba(255,255,255,.74);font-size:14px;line-height:1.9}.visual-copy ul{max-width:570px;margin:38px 0 0;padding:0;display:grid;gap:12px;list-style:none}.visual-copy li{display:flex;align-items:center;gap:14px;padding:14px 16px;border:1px solid rgba(255,255,255,.13);border-radius:13px;background:rgba(255,255,255,.07);backdrop-filter:blur(8px)}.visual-copy li>b{width:34px;height:34px;display:grid;place-items:center;border-radius:9px;background:rgba(255,255,255,.13);font-size:10px}.visual-copy li span,.visual-copy li strong,.visual-copy li small{display:block}.visual-copy li span{flex:1}.visual-copy li strong{font-size:12px}.visual-copy li small{margin-top:5px;color:rgba(255,255,255,.59);font-size:10px}.register-form-panel{min-height:100vh;padding:36px clamp(38px,6vw,82px);display:flex;flex-direction:column;justify-content:center;background:#fff}.register-card{width:100%;max-width:520px;margin:auto}.mobile-brand{display:none;margin-bottom:36px}.eyebrow{color:#2563eb}.register-card h2{margin:13px 0 8px;color:#172033;font-size:27px;letter-spacing:-.025em}.subtitle{margin:0 0 25px;color:#8390a3;font-size:13px;line-height:1.7}.register-card :deep(.el-form-item){margin-bottom:19px}.register-card :deep(.el-form-item__label){color:#42506a;font-size:12px;font-weight:650}.register-card :deep(.el-input__wrapper){min-height:44px;border-radius:10px;box-shadow:0 0 0 1px #dfe5ee inset}.register-card :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 1px #2563eb inset,0 0 0 3px #dbeafe}.password-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.full-button{width:100%;height:46px;margin-top:3px;border-radius:10px;font-weight:700}.login-link{margin:18px 0 0;color:#7b8798;font-size:12px;text-align:center}.login-link a{color:#2563eb;font-weight:700;text-decoration:none}.login-link a:hover{text-decoration:underline}.footer{margin:28px 0 0;color:#a4adba;font-size:10px;text-align:center}.register-visual{min-width:0}.register-form-panel{min-width:0}
@media(max-width:1080px){.register-page{grid-template-columns:minmax(400px,.85fr) minmax(480px,1fr)}.password-grid{grid-template-columns:1fr}}
@media(max-width:860px){.register-page{grid-template-columns:1fr}.register-visual{display:none}.register-form-panel{padding:30px 24px}.mobile-brand{display:block}.register-card{max-width:480px}}
</style>
