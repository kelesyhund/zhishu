import client from './client'

export type RetrievalMode = 'VECTOR' | 'HYBRID'
export type RetrievalFusionMethod = 'WEIGHTED' | 'RRF'

export interface RetrievalConfig {
  retrieval_mode: RetrievalMode
  retrieval_top_k: number
  similarity_threshold: number
  vector_weight: number
  fusion_method: RetrievalFusionMethod
  vector_candidate_k: number
  keyword_candidate_k: number
  rrf_k: number
  rerank_enabled: boolean
  rerank_candidate_k: number
  max_context_chars: number
  system_prompt: string
  no_answer_message: string
}

export interface RetrievalEffectiveConfig {
  mode: RetrievalMode
  fusion_method: RetrievalFusionMethod
  top_k: number
  threshold: number
  vector_weight: number
  keyword_weight: number
  vector_candidate_k: number
  keyword_candidate_k: number
  rrf_k: number
  rerank_enabled: boolean
  rerank_candidate_k: number
  max_context_chars: number
}

export interface RetrievalDebugItem {
  paragraph_id: number
  document_id: number
  document_name: string
  position: number
  content: string
  parent_id: number | null
  parent_position: number | null
  parent_content: string
  heading_path: string[]
  page_start: number | null
  page_end: number | null
  source_block_ids: string[]
  vector_score_raw: number
  vector_score_normalized: number
  vector_rank: number | null
  keyword_score_raw: number
  keyword_score: number
  keyword_rank: number | null
  weighted_score: number
  rrf_score_raw: number | null
  rrf_score_normalized: number | null
  rrf_rank: number | null
  pre_rerank_rank: number | null
  rerank_score: number | null
  rerank_rank: number | null
  final_rank: number | null
  final_score: number
  included: boolean
  content_truncated: boolean
  exclusion_reason: string
}

export interface RetrievalDebugResult {
  query: string
  effective_config: RetrievalEffectiveConfig
  items: RetrievalDebugItem[]
  selected_count: number
  candidate_count: number
  context_chars: number
  latency_ms: number
  stage_timings: {
    preparation_ms: number
    ranking_ms: number
    rerank_ms: number
  }
  rerank_applied: boolean
  rerank_fallback_code: string
  rerank_fallback_reason: string
}

export interface RetrievalCapabilities {
  rrf_available: boolean
  reranker_configured: boolean
  reranker_ready: boolean
  reranker_model: string
  device: string
}

export type RetrievalExperiment = Partial<
  Pick<
    RetrievalConfig,
    | 'retrieval_mode'
    | 'fusion_method'
    | 'retrieval_top_k'
    | 'similarity_threshold'
    | 'vector_weight'
    | 'vector_candidate_k'
    | 'keyword_candidate_k'
    | 'rrf_k'
    | 'rerank_enabled'
    | 'rerank_candidate_k'
    | 'max_context_chars'
  >
>

export interface RetrievalRankChange {
  paragraph_id: number
  baseline_rank: number | null
  experimental_rank: number | null
  rank_change: number | null
  change_type: 'ADDED' | 'REMOVED' | 'MOVED' | 'UNCHANGED'
}

export interface RetrievalCompareResult {
  query: string
  baseline: RetrievalDebugResult
  experimental: RetrievalDebugResult
  changes: RetrievalRankChange[]
}

export type AgentRunStatus = 'RUNNING' | 'SUCCESS' | 'FAILURE' | 'LIMIT_REACHED' | 'CANCELLED'
export type ToolExecutionStatus = 'RUNNING' | 'SUCCESS' | 'FAILURE' | 'REJECTED'

export interface AvailableAgentTool {
  name: string
  label: string
  description: string
}

export interface AgentChatModelStatus {
  available: boolean
  source: 'DATABASE' | 'ENVIRONMENT' | 'LOCAL'
  label: string
}

export interface AgentConfig {
  agent_enabled: boolean
  agent_max_steps: number
  agent_system_prompt: string
  enabled_tools: string[]
  available_tools: AvailableAgentTool[]
  chat_model_status: AgentChatModelStatus
}

export interface AgentConfigWrite {
  agent_enabled: boolean
  agent_max_steps: number
  agent_system_prompt: string
  enabled_tools: string[]
}

export interface ToolExecutionItem {
  id: number
  step: number
  sequence: number
  tool_call_id: string
  tool_name: string
  arguments: Record<string, unknown>
  result_summary: string
  result_payload: Record<string, unknown>
  status: ToolExecutionStatus
  latency_ms: number
  error_code: string
  error_message: string
  created_at?: string
  finished_at?: string | null
}

export interface AgentRunTrace {
  id: number
  status: AgentRunStatus
  step_count: number
  error_code: string
  error_message: string
  user_message_id?: number
  assistant_message_id?: number | null
  started_at?: string
  finished_at?: string | null
  tool_executions: ToolExecutionItem[]
}

export async function getRetrievalConfig(knowledgeId: number): Promise<RetrievalConfig> {
  return (await client.get(`/knowledge-bases/${knowledgeId}/retrieval-config/`)).data.data
}

export async function updateRetrievalConfig(
  knowledgeId: number,
  data: RetrievalConfig,
): Promise<RetrievalConfig> {
  return (await client.patch(`/knowledge-bases/${knowledgeId}/retrieval-config/`, data)).data.data
}

export async function debugRetrieval(
  knowledgeId: number,
  query: string,
  candidateLimit = 20,
): Promise<RetrievalDebugResult> {
  return (
    await client.post(`/knowledge-bases/${knowledgeId}/retrieval/debug/`, {
      query,
      candidate_limit: candidateLimit,
    })
  ).data.data
}

export async function getRetrievalCapabilities(): Promise<RetrievalCapabilities> {
  return (await client.get('/retrieval/capabilities/')).data.data
}

export async function compareRetrieval(
  knowledgeId: number,
  query: string,
  experimental: RetrievalExperiment,
  candidateLimit = 20,
): Promise<RetrievalCompareResult> {
  return (
    await client.post(`/knowledge-bases/${knowledgeId}/retrieval/compare/`, {
      query,
      candidate_limit: candidateLimit,
      experimental,
    })
  ).data.data
}

export async function getAgentConfig(knowledgeId: number): Promise<AgentConfig> {
  return (await client.get(`/knowledge-bases/${knowledgeId}/agent-config/`)).data.data
}

export async function updateAgentConfig(
  knowledgeId: number,
  data: AgentConfigWrite,
): Promise<AgentConfig> {
  return (await client.patch(`/knowledge-bases/${knowledgeId}/agent-config/`, data)).data.data
}

export async function getAgentRun(
  knowledgeId: number,
  agentRunId: number,
): Promise<AgentRunTrace> {
  return (await client.get(`/knowledge-bases/${knowledgeId}/agent-runs/${agentRunId}/`)).data.data
}
