<script setup lang="ts">
import type { RetrievalCapabilities, RetrievalCompareResult, RetrievalConfig, RetrievalDebugResult } from '../../../api'

defineProps<{
  form: RetrievalConfig
  keywordWeight: number
  configLoading: boolean
  configSaving: boolean
  capabilities: RetrievalCapabilities | null
  capabilitiesLoading: boolean
  debugLoading: boolean
  debugResult: RetrievalDebugResult | null
  compareLoading: boolean
  compareResult: RetrievalCompareResult | null
  scoreText: (value: number | null) => string
}>()
const settingsVisible = defineModel<boolean>('settingsVisible', { required: true })
const debugVisible = defineModel<boolean>('debugVisible', { required: true })
const query = defineModel<string>('query', { required: true })
const emit = defineEmits<{ reset: []; save: []; debug: []; compare: [] }>()
</script>

<template>
  <el-drawer v-model="settingsVisible" size="min(600px, 94vw)">
    <template #header><div class="retrieval-drawer-title"><strong>知识库检索设置</strong><span>控制正式搜索、问答引用和发送给模型的上下文</span></div></template>
    <div v-loading="configLoading" class="retrieval-settings-form">
      <el-form label-position="top">
        <el-form-item label="检索模式"><el-radio-group v-model="form.retrieval_mode"><el-radio-button value="VECTOR">纯向量</el-radio-button><el-radio-button value="HYBRID">混合检索</el-radio-button></el-radio-group><p class="field-help">混合检索同时考虑语义相似度和关键词 BM25 分数。</p></el-form-item>
        <el-form-item v-if="form.retrieval_mode === 'HYBRID'" label="融合方式"><el-radio-group v-model="form.fusion_method"><el-radio-button value="WEIGHTED">分数加权</el-radio-button><el-radio-button value="RRF">RRF 排名融合</el-radio-button></el-radio-group><p class="field-help">RRF分别截取向量和BM25候选，再按两个排名融合。</p></el-form-item>
        <div class="retrieval-number-grid"><el-form-item label="返回切片数量"><el-input-number v-model="form.retrieval_top_k" :min="1" :max="20" /></el-form-item><el-form-item label="最大上下文字符数"><el-input-number v-model="form.max_context_chars" :min="1000" :max="30000" :step="500" /></el-form-item></div>
        <el-form-item :label="`最低相关度阈值：${form.similarity_threshold.toFixed(2)}`"><el-slider v-model="form.similarity_threshold" :min="0" :max="1" :step="0.05" show-stops /></el-form-item>
        <el-form-item v-if="form.fusion_method === 'WEIGHTED'" :label="`向量权重：${form.vector_weight.toFixed(2)}（关键词权重：${keywordWeight.toFixed(2)}）`"><el-slider v-model="form.vector_weight" :min="0" :max="1" :step="0.05" show-stops /></el-form-item>
        <template v-if="form.retrieval_mode === 'HYBRID' && form.fusion_method === 'RRF'">
          <div class="retrieval-number-grid"><el-form-item label="向量召回候选"><el-input-number v-model="form.vector_candidate_k" :min="1" :max="100" /></el-form-item><el-form-item label="BM25召回候选"><el-input-number v-model="form.keyword_candidate_k" :min="1" :max="100" /></el-form-item><el-form-item label="RRF平滑参数"><el-input-number v-model="form.rrf_k" :min="1" :max="200" /></el-form-item><el-form-item label="重排候选数量"><el-input-number v-model="form.rerank_candidate_k" :min="1" :max="50" /></el-form-item></div>
          <el-form-item label="Cross-Encoder精排"><el-switch v-model="form.rerank_enabled" :disabled="capabilitiesLoading || !capabilities?.reranker_ready" active-text="启用" inactive-text="关闭" /><p class="field-help">{{ capabilities?.reranker_ready ? `当前模型：${capabilities.reranker_model}（${capabilities.device}）` : '服务器尚未准备本地重排模型；RRF仍可正常使用。' }}</p></el-form-item>
        </template>
        <el-form-item label="System Prompt"><el-input v-model="form.system_prompt" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="留空使用系统默认" /></el-form-item>
        <el-form-item label="资料不足提示"><el-input v-model="form.no_answer_message" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="留空使用系统默认提示" /></el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="设置只影响当前知识库；恢复默认值后仍需点击保存。" />
    </div>
    <template #footer><el-button :disabled="configSaving" @click="emit('reset')">恢复默认值</el-button><el-button :disabled="configSaving" @click="settingsVisible = false">取消</el-button><el-button type="primary" :loading="configSaving" @click="emit('save')">保存设置</el-button></template>
  </el-drawer>

  <el-drawer v-model="debugVisible" size="min(820px, 96vw)">
    <template #header><div class="retrieval-drawer-title"><strong>检索调试工作台</strong><span>只执行检索，不调用 Chat 模型，也不会创建会话</span></div></template>
    <div class="retrieval-debug-toolbar"><el-input v-model="query" type="textarea" :rows="2" maxlength="1000" placeholder="输入一个问题，查看各切片的评分和排除原因" @keydown.ctrl.enter.prevent="emit('debug')" /><el-button type="primary" :loading="debugLoading" @click="emit('debug')">执行检索</el-button><el-button :loading="compareLoading" @click="emit('compare')">A/B 对比</el-button></div>
    <div v-loading="debugLoading || compareLoading" class="retrieval-debug-content">
      <el-empty v-if="!debugLoading && !compareLoading && !debugResult && !compareResult" description="输入问题后执行检索或A/B策略对比" />
      <template v-if="debugResult">
        <div class="retrieval-debug-summary"><div><span>模式</span><strong>{{ debugResult.effective_config.mode }} / {{ debugResult.effective_config.fusion_method }}</strong></div><div><span>候选</span><strong>{{ debugResult.candidate_count }}</strong></div><div><span>入选</span><strong>{{ debugResult.selected_count }}</strong></div><div><span>上下文字符</span><strong>{{ debugResult.context_chars }}</strong></div><div><span>耗时</span><strong>{{ debugResult.latency_ms }} ms</strong></div></div>
        <p class="retrieval-effective-line">top_k {{ debugResult.effective_config.top_k }} · 阈值 {{ scoreText(debugResult.effective_config.threshold) }} · 向量权重 {{ scoreText(debugResult.effective_config.vector_weight) }} · 关键词权重 {{ scoreText(debugResult.effective_config.keyword_weight) }} · 准备 {{ debugResult.stage_timings.preparation_ms }}ms · 排序 {{ debugResult.stage_timings.ranking_ms }}ms · 重排 {{ debugResult.stage_timings.rerank_ms }}ms</p>
        <el-alert v-if="debugResult.rerank_fallback_reason" type="warning" :closable="false" :title="debugResult.rerank_fallback_reason" />
        <el-empty v-if="!debugResult.items.length" description="没有状态成功且向量版本兼容的候选切片" />
        <article v-for="item in debugResult.items" :key="item.paragraph_id" class="retrieval-candidate">
          <div class="retrieval-candidate-head"><div><strong>{{ item.document_name }} · 切片 #{{ item.position }}</strong><span>Paragraph {{ item.paragraph_id }}<template v-if="item.parent_position"> · Parent #{{ item.parent_position }}</template></span></div><el-tag :type="item.included ? 'success' : 'info'">{{ item.included ? '进入上下文' : item.exclusion_reason }}</el-tag></div>
          <div class="retrieval-scores"><span>向量分 / 排名 <strong>{{ scoreText(item.vector_score_normalized) }} / {{ item.vector_rank ?? '—' }}</strong></span><span>BM25分 / 排名 <strong>{{ scoreText(item.keyword_score) }} / {{ item.keyword_rank ?? '—' }}</strong></span><span>RRF分 / 排名 <strong>{{ scoreText(item.rrf_score_normalized) }} / {{ item.rrf_rank ?? '—' }}</strong></span><span>重排分 / 排名 <strong>{{ scoreText(item.rerank_score) }} / {{ item.rerank_rank ?? '—' }}</strong></span><span>最终排名 <strong>{{ item.final_rank ?? '—' }}</strong></span><span>最终分 <strong>{{ scoreText(item.final_score) }}</strong></span></div>
          <details class="retrieval-content-details"><summary>展开查看命中的Child正文</summary><p>{{ item.content }}</p></details><details v-if="item.parent_content" class="retrieval-content-details"><summary>展开查看生成阶段使用的Parent正文</summary><p>{{ item.parent_content }}</p></details>
        </article>
      </template>
      <section v-if="compareResult" class="retrieval-compare">
        <header><div><strong>当前策略</strong><span>{{ compareResult.baseline.effective_config.mode }} / {{ compareResult.baseline.effective_config.fusion_method }}</span></div><div><strong>实验策略</strong><span>{{ compareResult.experimental.effective_config.mode }} / {{ compareResult.experimental.effective_config.fusion_method }}</span></div></header>
        <el-alert v-if="compareResult.experimental.rerank_fallback_reason" type="warning" :closable="false" :title="compareResult.experimental.rerank_fallback_reason" />
        <div class="retrieval-compare-list"><div v-for="change in compareResult.changes" :key="change.paragraph_id" :class="['retrieval-rank-change', change.change_type.toLowerCase()]"><strong>Paragraph {{ change.paragraph_id }}</strong><span>当前 {{ change.baseline_rank ?? '未召回' }} → 实验 {{ change.experimental_rank ?? '未召回' }}</span><el-tag size="small" :type="change.change_type === 'ADDED' ? 'success' : change.change_type === 'REMOVED' ? 'danger' : 'info'">{{ change.change_type }}</el-tag></div></div>
      </section>
    </div>
  </el-drawer>
</template>
