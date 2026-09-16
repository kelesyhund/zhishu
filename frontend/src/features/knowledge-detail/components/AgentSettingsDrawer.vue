<script setup lang="ts">
import type { AgentConfig } from '../../../api'

defineProps<{
  config: AgentConfig | null
  loading: boolean
  saving: boolean
  form: { agent_enabled: boolean; agent_max_steps: number; agent_system_prompt: string; enabled_tools: string[] }
}>()
const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ reset: []; save: [] }>()
</script>

<template>
  <el-drawer v-model="visible" size="min(600px, 94vw)">
    <template #header><div class="agent-settings-title"><strong>Agent 设置</strong><span>控制当前知识库是否允许模型调用后端安全工具</span></div></template>
    <div v-loading="loading" class="agent-settings-form">
      <el-alert v-if="config && !config.chat_model_status.available" type="warning" :closable="false" title="当前没有可用的 Chat 模型；开启 Agent 后需要先配置模型才能发送。" />
      <el-form label-position="top">
        <el-form-item label="Agent 模式"><el-switch v-model="form.agent_enabled" active-text="开启" inactive-text="关闭，使用普通 RAG" /></el-form-item>
        <el-form-item label="当前 Chat 模型"><div class="agent-model-line"><el-tag :type="config?.chat_model_status.available ? 'success' : 'info'">{{ config?.chat_model_status.label || '未读取' }}</el-tag><span>{{ config?.chat_model_status.source || 'LOCAL' }}</span></div></el-form-item>
        <el-form-item label="最大执行步骤"><el-input-number v-model="form.agent_max_steps" :min="1" :max="10" /><p class="field-help">达到上限后会安全停止，不会把未完成内容保存成成功回答。</p></el-form-item>
        <el-form-item label="允许使用的工具">
          <el-checkbox-group v-model="form.enabled_tools" class="agent-tool-options"><el-checkbox v-for="tool in config?.available_tools || []" :key="tool.name" :value="tool.name"><span class="agent-tool-label"><strong>{{ tool.label }}</strong>{{ tool.description }}</span></el-checkbox></el-checkbox-group>
          <p class="field-help">不勾选表示没有工具权限，Agent只能直接回答。</p>
        </el-form-item>
        <el-form-item label="Agent System Prompt"><el-input v-model="form.agent_system_prompt" type="textarea" :rows="5" maxlength="2000" show-word-limit placeholder="留空使用系统默认；这里仅补充业务风格，不能覆盖固定安全约束" /></el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="页面只展示实际工具调用记录，不展示模型隐藏思维链。" />
    </div>
    <template #footer><el-button :disabled="saving" @click="emit('reset')">恢复默认值</el-button><el-button :disabled="saving" @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="emit('save')">保存设置</el-button></template>
  </el-drawer>
</template>
