import client from './client'

export interface KnowledgeBase {
  id: number
  name: string
  description: string
  document_count: number
  created_at: string
}

export type OrganizationRole = 'OWNER' | 'ADMIN' | 'MEMBER' | 'AUDITOR'
export type WorkspaceRole = 'ADMIN' | 'DEVELOPER' | 'OPERATOR' | 'VIEWER' | 'AUDITOR'

export interface OrganizationItem {
  id: number
  name: string
  slug: string
  status: 'ACTIVE' | 'DISABLED'
  current_user_role: OrganizationRole
  member_count: number
  workspace_count: number
  created_at: string
  updated_at: string
}

export interface WorkspaceItem {
  id: number
  organization_id: number
  organization_name: string
  name: string
  slug: string
  status: 'ACTIVE' | 'DISABLED'
  is_default: boolean
  current_user_role: WorkspaceRole
  capabilities: string[]
  member_count: number
  created_at: string
  updated_at: string
}

export interface OrganizationMembershipItem {
  id: number
  user_id: number
  username: string
  role: OrganizationRole
  created_at: string
  updated_at: string
}

export interface WorkspaceMembershipItem {
  id: number
  user_id: number
  username: string
  role: WorkspaceRole
  created_at: string
  updated_at: string
}

export interface AuditEventItem {
  id: number
  organization_id: number
  workspace_id: number | null
  workspace_name: string | null
  actor_id: number | null
  actor_username: string | null
  action: string
  resource_type: string
  resource_id: string
  result: 'SUCCESS' | 'FAILURE' | 'REJECTED'
  request_id: string
  ip_hash: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface MeContext {
  organizations: OrganizationItem[]
  workspaces: WorkspaceItem[]
  active_workspace_id: number | null
}

export async function getMeContext(): Promise<MeContext> {
  return (await client.get('/me/context/')).data.data
}

export async function listOrganizations(): Promise<PageResult<OrganizationItem>> {
  return (await client.get('/organizations/', { params: { page_size: 100 } })).data.data
}

export async function createOrganization(name: string): Promise<OrganizationItem> {
  return (await client.post('/organizations/', { name })).data.data
}

export async function listOrganizationMembers(id: number): Promise<PageResult<OrganizationMembershipItem>> {
  return (await client.get(`/organizations/${id}/members/`, { params: { page_size: 100 } })).data.data
}

export async function addOrganizationMember(id: number, username: string, role: OrganizationRole) {
  return (await client.post(`/organizations/${id}/members/`, { username, role })).data.data as OrganizationMembershipItem
}

export async function updateOrganizationMember(id: number, membershipId: number, role: OrganizationRole) {
  return (await client.patch(`/organizations/${id}/members/${membershipId}/`, { role })).data.data as OrganizationMembershipItem
}

export async function removeOrganizationMember(id: number, membershipId: number) {
  await client.delete(`/organizations/${id}/members/${membershipId}/`)
}

export async function listOrganizationWorkspaces(id: number): Promise<PageResult<WorkspaceItem>> {
  return (await client.get(`/organizations/${id}/workspaces/`, { params: { page_size: 100 } })).data.data
}

export async function createWorkspace(id: number, name: string): Promise<WorkspaceItem> {
  return (await client.post(`/organizations/${id}/workspaces/`, { name })).data.data
}

export async function listWorkspaceMembers(id: number): Promise<PageResult<WorkspaceMembershipItem>> {
  return (await client.get(`/workspaces/${id}/members/`, {
    params: { page_size: 100 }, headers: { 'X-Workspace-ID': String(id) },
  })).data.data
}

export async function addWorkspaceMember(id: number, username: string, role: WorkspaceRole) {
  return (await client.post(`/workspaces/${id}/members/`, { username, role }, {
    headers: { 'X-Workspace-ID': String(id) },
  })).data.data as WorkspaceMembershipItem
}

export async function updateWorkspaceMember(id: number, membershipId: number, role: WorkspaceRole) {
  return (await client.patch(`/workspaces/${id}/members/${membershipId}/`, { role }, {
    headers: { 'X-Workspace-ID': String(id) },
  })).data.data as WorkspaceMembershipItem
}

export async function removeWorkspaceMember(id: number, membershipId: number) {
  await client.delete(`/workspaces/${id}/members/${membershipId}/`, {
    headers: { 'X-Workspace-ID': String(id) },
  })
}

export async function listAuditEvents(params: Record<string, string | number> = {}): Promise<PageResult<AuditEventItem>> {
  return (await client.get('/audit-events/', { params })).data.data
}

export interface KnowledgeBasePage {
  items: KnowledgeBase[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface KnowledgeBaseListParams {
  keyword?: string
  page?: number
  page_size?: number
}

export interface DocumentItem {
  id: number
  name: string
  status: 'PROCESSING' | 'SUCCESS' | 'FAILURE'
  error_message: string
  paragraph_count: number
  parent_chunk_count: number
  needs_reprocess: boolean
  source_id: string
  source_sha256: string
  parser_type: DocumentParserType
  chunk_strategy: ChunkStrategy
  parent_max_tokens: number
  child_target_tokens: number
  child_overlap_tokens: number
  preserve_tables: boolean
  preserve_code_blocks: boolean
  parser_version: string
  chunker_version: string
  parsing_warnings: string[]
  created_at: string
}

export type DocumentParserType = 'AUTO' | 'TXT' | 'MARKDOWN' | 'PDF' | 'DOCX'
export type ChunkStrategy = 'LEGACY' | 'PARENT_CHILD'

export interface DocumentChunkingConfig {
  parser_type: DocumentParserType
  chunk_strategy: ChunkStrategy
  parent_max_tokens: number
  child_target_tokens: number
  child_overlap_tokens: number
  preserve_tables: boolean
  preserve_code_blocks: boolean
}

export interface ChunkPreviewChild {
  position: number
  heading_path: string[]
  page_start: number | null
  page_end: number | null
  structure_type: 'TEXT' | 'TABLE' | 'CODE' | 'LIST' | 'MIXED'
  token_count: number
  content: string
  source_block_ids: string[]
}

export interface ChunkPreviewParent {
  position: number | null
  heading_path: string[]
  page_start: number | null
  page_end: number | null
  structure_type: 'TEXT' | 'TABLE' | 'CODE' | 'LIST' | 'MIXED'
  token_count: number
  content: string
  source_block_ids: string[]
  children: ChunkPreviewChild[]
}

export interface DocumentChunkPreview {
  parser_type: DocumentParserType
  parser_version: string
  chunker_version: string
  source_sha256: string
  block_count: number
  parent_count: number
  child_count: number
  warnings: string[]
  truncated: boolean
  items: ChunkPreviewParent[]
}

export type DocumentTaskType = 'UPLOAD' | 'REPROCESS'
export type DocumentTaskStatus =
  | 'PENDING'
  | 'PROCESSING'
  | 'RETRYING'
  | 'SUCCESS'
  | 'FAILURE'
  | 'ENQUEUE_FAILED'
  | 'CANCEL_REQUESTED'
  | 'CANCELLED'

export type DocumentTaskStage =
  | 'WAITING'
  | 'READING'
  | 'SPLITTING'
  | 'EMBEDDING'
  | 'SAVING'
  | 'DONE'
  | 'FAILED'
  | 'CANCELLING'
  | 'CANCELLED'

export interface DocumentProcessingTaskItem {
  id: number
  document_id: number
  document_name: string
  task_type: DocumentTaskType
  status: DocumentTaskStatus
  progress: number
  current_stage: DocumentTaskStage
  attempt_count: number
  error_message: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export interface DocumentProcessingTaskPage {
  items: DocumentProcessingTaskItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface AsyncDocumentResult {
  document: DocumentItem
  task: DocumentProcessingTaskItem
}

export type ModelType = 'CHAT' | 'EMBEDDING'
export type ModelTestStatus = 'UNTESTED' | 'SUCCESS' | 'FAILURE'

export interface ModelConfigItem {
  id: number
  name: string
  model_type: ModelType
  base_url: string
  model_name: string
  timeout_seconds: number
  revision: number
  api_key_configured: boolean
  api_key_masked: string
  last_test_status: ModelTestStatus
  last_test_message: string
  last_test_at: string | null
  embedding_dimension: number | null
  created_at: string
  updated_at: string
}

export interface ModelConfigPage {
  items: ModelConfigItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface ModelConfigWrite {
  name?: string
  model_type?: ModelType
  base_url?: string
  model_name?: string
  api_key?: string
  timeout_seconds?: number
}

export interface ModelTestResult {
  model_type: ModelType
  success: boolean
  latency_ms: number
  embedding_dimension: number | null
  message: string
}

export interface EffectiveModelStatus {
  source: 'DATABASE' | 'ENVIRONMENT' | 'LOCAL'
  config_id: number | null
  label: string
  model_name: string
}

export interface KnowledgeBaseModelStatus {
  chat_model_config_id: number | null
  embedding_model_config_id: number | null
  chat: EffectiveModelStatus
  embedding: EffectiveModelStatus
  embedding_stale_document_count: number
}

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

export interface ParagraphItem {
  id: number
  position: number
  content: string
  chunk_type: 'LEGACY' | 'CHILD'
  parent_id: number | null
  parent_position: number | null
  heading_path: string[]
  page_start: number | null
  page_end: number | null
  token_count: number
  content_sha256: string
  source_block_ids: string[]
  structure_type: 'TEXT' | 'TABLE' | 'CODE' | 'LIST' | 'MIXED'
}

export interface ParagraphPage {
  items: ParagraphItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface ReferenceItem {
  document_name: string
  content: string
  similarity: number
  knowledge_base_id?: number
  knowledge_base_name?: string
  application_rrf_score?: number
  final_rank?: number
}

export interface ConversationItem {
  id: number
  title: string
  message_count: number
  created_at: string
  last_message_at: string | null
}

export interface ConversationPage {
  items: ConversationItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface MessageItem {
  id: number
  role: 'user' | 'assistant'
  content: string
  references: ReferenceItem[]
  agent_trace: AgentRunTrace | null
  created_at: string
}

export interface MessagePage {
  items: MessageItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
  has_previous: boolean
  previous_page: number | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export async function login(username: string, password: string) {
  return (await client.post('/login/', { username, password })).data.data
}

export interface RegisterPayload {
  username: string
  email?: string
  password: string
  password_confirm: string
}

export interface AuthResult {
  token: string
  username: string
}

export async function register(payload: RegisterPayload): Promise<AuthResult> {
  return (await client.post('/register/', payload)).data.data
}

export async function listKnowledgeBases(params: KnowledgeBaseListParams = {}): Promise<KnowledgeBasePage> {
  return (await client.get('/knowledge-bases/', { params })).data.data
}

export async function createKnowledgeBase(data: { name: string; description: string }) {
  return (await client.post('/knowledge-bases/', data)).data.data as KnowledgeBase
}

export async function getKnowledgeBase(id: number): Promise<KnowledgeBase> {
  return (await client.get(`/knowledge-bases/${id}/`)).data.data
}

export async function updateKnowledgeBase(
  id: number,
  data: { name: string; description: string },
): Promise<KnowledgeBase> {
  return (await client.patch(`/knowledge-bases/${id}/`, data)).data.data
}

export async function deleteKnowledgeBase(id: number) {
  await client.delete(`/knowledge-bases/${id}/`)
}

export async function listModelConfigs(
  modelType: ModelType,
  page = 1,
  pageSize = 20,
): Promise<ModelConfigPage> {
  return (
    await client.get('/model-configs/', {
      params: { model_type: modelType, page, page_size: pageSize },
    })
  ).data.data
}

export async function createModelConfig(data: ModelConfigWrite): Promise<ModelConfigItem> {
  return (await client.post('/model-configs/', data)).data.data
}

export async function getModelConfig(id: number): Promise<ModelConfigItem> {
  return (await client.get(`/model-configs/${id}/`)).data.data
}

export async function updateModelConfig(
  id: number,
  data: ModelConfigWrite,
): Promise<ModelConfigItem> {
  return (await client.patch(`/model-configs/${id}/`, data)).data.data
}

export async function deleteModelConfig(id: number): Promise<void> {
  await client.delete(`/model-configs/${id}/`)
}

export async function testModelConfig(id: number): Promise<ModelTestResult> {
  return (await client.post(`/model-configs/${id}/test/`)).data.data
}

export async function getKnowledgeBaseModelStatus(
  knowledgeId: number,
): Promise<KnowledgeBaseModelStatus> {
  return (await client.get(`/knowledge-bases/${knowledgeId}/model-config/`)).data.data
}

export async function updateKnowledgeBaseModelStatus(
  knowledgeId: number,
  data: {
    chat_model_config_id?: number | null
    embedding_model_config_id?: number | null
  },
): Promise<KnowledgeBaseModelStatus> {
  return (await client.patch(`/knowledge-bases/${knowledgeId}/model-config/`, data)).data.data
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

export async function listDocuments(id: number): Promise<DocumentItem[]> {
  return (await client.get(`/knowledge-bases/${id}/documents/`)).data.data
}

export async function uploadDocument(
  id: number,
  file: File,
  idempotencyKey = crypto.randomUUID(),
): Promise<AsyncDocumentResult> {
  const data = new FormData()
  data.append('file', file)
  return (
    await client.post(`/knowledge-bases/${id}/documents/`, data, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data.data
}

export async function getDocument(knowledgeId: number, documentId: number): Promise<DocumentItem> {
  return (await client.get(`/knowledge-bases/${knowledgeId}/documents/${documentId}/`)).data.data
}

export async function deleteDocument(knowledgeId: number, documentId: number): Promise<void> {
  await client.delete(`/knowledge-bases/${knowledgeId}/documents/${documentId}/`)
}

export async function listDocumentParagraphs(
  knowledgeId: number,
  documentId: number,
  page = 1,
  pageSize = 20,
): Promise<ParagraphPage> {
  return (
    await client.get(`/knowledge-bases/${knowledgeId}/documents/${documentId}/paragraphs/`, {
      params: { page, page_size: pageSize },
    })
  ).data.data
}

export async function reprocessDocument(
  knowledgeId: number,
  documentId: number,
  idempotencyKey = crypto.randomUUID(),
): Promise<AsyncDocumentResult> {
  return (
    await client.post(`/knowledge-bases/${knowledgeId}/documents/${documentId}/reprocess/`, null, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data.data
}

export async function getDocumentChunkingConfig(
  knowledgeId: number,
  documentId: number,
): Promise<DocumentChunkingConfig> {
  return (
    await client.get(`/knowledge-bases/${knowledgeId}/documents/${documentId}/chunking-config/`)
  ).data.data
}

export async function updateDocumentChunkingConfig(
  knowledgeId: number,
  documentId: number,
  config: DocumentChunkingConfig,
): Promise<{ config: DocumentChunkingConfig; document: DocumentItem }> {
  return (
    await client.patch(
      `/knowledge-bases/${knowledgeId}/documents/${documentId}/chunking-config/`,
      config,
    )
  ).data.data
}

export async function previewDocumentChunks(
  knowledgeId: number,
  documentId: number,
  config: DocumentChunkingConfig,
): Promise<DocumentChunkPreview> {
  return (
    await client.post(
      `/knowledge-bases/${knowledgeId}/documents/${documentId}/chunk-preview/`,
      config,
    )
  ).data.data
}

export async function reindexDocument(
  knowledgeId: number,
  documentId: number,
  idempotencyKey = crypto.randomUUID(),
): Promise<AsyncDocumentResult> {
  return (
    await client.post(`/knowledge-bases/${knowledgeId}/documents/${documentId}/reindex/`, null, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
  ).data.data
}

export async function listDocumentProcessingTasks(
  knowledgeId: number,
  page = 1,
  pageSize = 100,
): Promise<DocumentProcessingTaskPage> {
  return (
    await client.get(`/knowledge-bases/${knowledgeId}/processing-tasks/`, {
      params: { page, page_size: pageSize },
    })
  ).data.data
}

export async function retryDocumentProcessingTask(
  knowledgeId: number,
  taskId: number,
): Promise<DocumentProcessingTaskItem> {
  return (
    await client.post(`/knowledge-bases/${knowledgeId}/processing-tasks/${taskId}/retry/`)
  ).data.data
}

export async function cancelDocumentProcessingTask(
  knowledgeId: number,
  taskId: number,
): Promise<DocumentProcessingTaskItem> {
  return (
    await client.post(`/knowledge-bases/${knowledgeId}/processing-tasks/${taskId}/cancel/`)
  ).data.data
}

export async function listConversations(
  knowledgeId: number,
  page = 1,
  pageSize = 20,
): Promise<ConversationPage> {
  return (
    await client.get(`/knowledge-bases/${knowledgeId}/conversations/`, {
      params: { page, page_size: pageSize },
    })
  ).data.data
}

export async function getConversation(
  knowledgeId: number,
  conversationId: number,
): Promise<ConversationItem> {
  return (
    await client.get(`/knowledge-bases/${knowledgeId}/conversations/${conversationId}/`)
  ).data.data
}

export async function updateConversationTitle(
  knowledgeId: number,
  conversationId: number,
  title: string,
): Promise<ConversationItem> {
  return (
    await client.patch(`/knowledge-bases/${knowledgeId}/conversations/${conversationId}/`, {
      title,
    })
  ).data.data
}

export async function deleteConversation(
  knowledgeId: number,
  conversationId: number,
): Promise<void> {
  await client.delete(`/knowledge-bases/${knowledgeId}/conversations/${conversationId}/`)
}

export async function listConversationMessages(
  knowledgeId: number,
  conversationId: number,
  page: number | 'last' = 'last',
  pageSize = 50,
): Promise<MessagePage> {
  return (
    await client.get(
      `/knowledge-bases/${knowledgeId}/conversations/${conversationId}/messages/`,
      { params: { page, page_size: pageSize } },
    )
  ).data.data
}

export async function searchKnowledge(id: number, query: string): Promise<ReferenceItem[]> {
  return (await client.post(`/knowledge-bases/${id}/search/`, { query })).data.data
}

export async function streamChat(
  id: number,
  message: string,
  conversationId: number | null,
  onEvent: (event: string, data: unknown) => void,
) {
  const response = await fetch(`/api/knowledge-bases/${id}/chat/stream/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Token ${localStorage.getItem('token') || ''}`,
      'X-Workspace-ID': localStorage.getItem('active_workspace_id') || '',
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
  if (!response.ok || !response.body) {
    const body: unknown = await response.json().catch(() => null)
    if (isRecord(body) && isRecord(body.data) && typeof body.data.conversation_id === 'number') {
      onEvent('meta', body.data)
    }
    const message = isRecord(body) && typeof body.message === 'string' ? body.message : '问答请求失败'
    throw new Error(message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      let event = 'message'
      let data = '{}'
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) data = line.slice(5).trim()
      }
      onEvent(event, JSON.parse(data))
    }
    if (done) break
  }
}

export type ApplicationStatus = 'DRAFT' | 'PUBLISHED' | 'DISABLED'

export interface ApplicationKnowledgeLink {
  knowledge_base_id: number
  knowledge_base_name: string
  position: number
  weight: number
  enabled: boolean
}

export interface ApplicationItem {
  id: number
  name: string
  description: string
  status: ApplicationStatus
  chat_model_config_id: number | null
  system_prompt: string
  welcome_message: string
  suggested_questions: string[]
  show_references: boolean
  agent_enabled: boolean
  agent_max_steps: number
  agent_system_prompt: string
  enabled_tools: string[]
  global_top_k: number
  max_context_chars: number
  knowledge_bases: ApplicationKnowledgeLink[]
  current_published_version_number: number | null
  version_count: number
  created_at: string
  updated_at: string
}

export interface ApplicationPage {
  items: ApplicationItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export type ApplicationWrite = Partial<Pick<
  ApplicationItem,
  | 'name'
  | 'description'
  | 'chat_model_config_id'
  | 'system_prompt'
  | 'welcome_message'
  | 'suggested_questions'
  | 'show_references'
  | 'agent_enabled'
  | 'agent_max_steps'
  | 'agent_system_prompt'
  | 'enabled_tools'
  | 'global_top_k'
  | 'max_context_chars'
>>

export interface ApplicationVersionItem {
  id: number
  version: number
  config_snapshot: Record<string, unknown>
  published_at: string
  created_at: string
}

export interface ApplicationCredentialItem {
  id: number
  name: string
  key_prefix: string
  api_key_masked: string
  enabled: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string
  api_key?: string
}

export interface ApplicationPublicAccess {
  enabled: boolean
  token_masked?: string
  public_token?: string
  allowed_frame_origins?: string[]
  created_at?: string
  rotated_at?: string
}

export interface ApplicationAccessLogItem {
  request_id: string
  access_type: string
  status: string
  status_code: number
  version: number | null
  credential_name: string | null
  first_token_latency_ms: number
  retrieval_latency_ms: number
  model_latency_ms: number
  total_latency_ms: number
  retrieved_paragraph_count: number
  error_code: string
  created_at: string
}

export interface PublicApplicationProfile {
  name: string
  description: string
  welcome_message: string
  suggested_questions: string[]
  show_references: boolean
  version: number
}

export async function listApplications(page = 1, pageSize = 20, search = ''): Promise<ApplicationPage> {
  return (await client.get('/applications/', { params: { page, page_size: pageSize, search } })).data.data
}

export async function createApplication(data: ApplicationWrite): Promise<ApplicationItem> {
  return (await client.post('/applications/', data)).data.data
}

export async function getApplication(id: number): Promise<ApplicationItem> {
  return (await client.get(`/applications/${id}/`)).data.data
}

export async function updateApplication(id: number, data: ApplicationWrite): Promise<ApplicationItem> {
  return (await client.patch(`/applications/${id}/`, data)).data.data
}

export async function deleteApplication(id: number): Promise<void> {
  await client.delete(`/applications/${id}/`)
}

export async function updateApplicationKnowledgeBases(
  id: number,
  knowledgeBases: Omit<ApplicationKnowledgeLink, 'knowledge_base_name'>[],
): Promise<ApplicationKnowledgeLink[]> {
  return (await client.put(`/applications/${id}/knowledge-bases/`, { knowledge_bases: knowledgeBases })).data.data
}

export async function publishApplication(id: number): Promise<ApplicationVersionItem> {
  return (await client.post(`/applications/${id}/publish/`)).data.data
}

export async function disableApplication(id: number): Promise<ApplicationItem> {
  return (await client.post(`/applications/${id}/disable/`)).data.data
}

export async function listApplicationVersions(id: number): Promise<ApplicationVersionItem[]> {
  return (await client.get(`/applications/${id}/versions/`, { params: { page_size: 100 } })).data.data.items
}

export async function rollbackApplication(id: number, versionId: number): Promise<ApplicationVersionItem> {
  return (await client.post(`/applications/${id}/versions/${versionId}/rollback/`)).data.data
}

export async function getApplicationPublicAccess(id: number): Promise<ApplicationPublicAccess> {
  return (await client.get(`/applications/${id}/public-access/`)).data.data
}

export async function enableApplicationPublicAccess(id: number): Promise<ApplicationPublicAccess> {
  return (await client.post(`/applications/${id}/public-access/enable/`)).data.data
}

export async function rotateApplicationPublicAccess(id: number): Promise<ApplicationPublicAccess> {
  return (await client.post(`/applications/${id}/public-access/rotate/`)).data.data
}

export async function disableApplicationPublicAccess(id: number): Promise<ApplicationPublicAccess> {
  return (await client.post(`/applications/${id}/public-access/disable/`)).data.data
}

export async function updateApplicationFrameOrigins(id: number, origins: string[]): Promise<ApplicationPublicAccess> {
  return (await client.patch(`/applications/${id}/public-access/`, { allowed_frame_origins: origins })).data.data
}

export async function listApplicationCredentials(id: number): Promise<ApplicationCredentialItem[]> {
  return (await client.get(`/applications/${id}/credentials/`)).data.data
}

export async function createApplicationCredential(id: number, name: string): Promise<ApplicationCredentialItem> {
  return (await client.post(`/applications/${id}/credentials/`, { name })).data.data
}

export async function updateApplicationCredential(id: number, credentialId: number, enabled: boolean): Promise<ApplicationCredentialItem> {
  return (await client.patch(`/applications/${id}/credentials/${credentialId}/`, { enabled })).data.data
}

export async function deleteApplicationCredential(id: number, credentialId: number): Promise<void> {
  await client.delete(`/applications/${id}/credentials/${credentialId}/`)
}

export async function listApplicationAccessLogs(id: number): Promise<ApplicationAccessLogItem[]> {
  return (await client.get(`/applications/${id}/access-logs/`, { params: { page_size: 50 } })).data.data.items
}

async function readSseResponse(response: Response, onEvent: (event: string, data: unknown) => void) {
  if (!response.ok || !response.body) {
    const body: unknown = await response.json().catch(() => null)
    throw new Error(isRecord(body) && typeof body.message === 'string' ? body.message : '问答请求失败')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      let event = 'message'
      let data = '{}'
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) data = line.slice(5).trim()
      }
      onEvent(event, JSON.parse(data))
    }
    if (done) break
  }
}

export async function streamApplicationPreview(
  id: number,
  message: string,
  conversationId: number | null,
  onEvent: (event: string, data: unknown) => void,
) {
  const response = await fetch(`/api/applications/${id}/preview/chat/stream/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Token ${localStorage.getItem('token') || ''}`,
      'X-Workspace-ID': localStorage.getItem('active_workspace_id') || '',
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
  return readSseResponse(response, onEvent)
}

export async function getPublicApplicationProfile(token: string): Promise<PublicApplicationProfile> {
  return (await client.get(`/public/applications/${encodeURIComponent(token)}/profile/`)).data.data
}

export async function createPublicVisitor(token: string): Promise<string> {
  return (await client.post(`/public/applications/${encodeURIComponent(token)}/visitor/`)).data.data.visitor_token
}

export async function listPublicApplicationMessages(
  token: string,
  visitorToken: string,
  conversationId: number,
): Promise<MessagePage> {
  return (
    await client.get(
      `/public/applications/${encodeURIComponent(token)}/conversations/${conversationId}/messages/`,
      { headers: { 'X-Visitor-Token': visitorToken }, params: { page: 'last', page_size: 100 } },
    )
  ).data.data
}

export async function streamPublicApplicationChat(
  token: string,
  visitorToken: string,
  message: string,
  conversationId: number | null,
  onEvent: (event: string, data: unknown) => void,
) {
  const response = await fetch(`/api/public/applications/${encodeURIComponent(token)}/chat/stream/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Visitor-Token': visitorToken },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
  return readSseResponse(response, onEvent)
}
