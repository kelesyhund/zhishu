import client, { csrfToken } from './client'
import type { MessagePage, ReferenceItem } from './conversations'
import { readSseResponse, type SseEventHandler } from './sse'

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

export async function streamApplicationPreview(
  id: number,
  message: string,
  conversationId: number | null,
  onEvent: SseEventHandler,
  signal?: AbortSignal,
) {
  const response = await fetch(`/api/applications/${id}/preview/chat/stream/`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken(),
      'X-Workspace-ID': localStorage.getItem('active_workspace_id') || '',
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  })
  return readSseResponse(response, onEvent, signal)
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
  onEvent: SseEventHandler,
  signal?: AbortSignal,
) {
  const response = await fetch(`/api/public/applications/${encodeURIComponent(token)}/chat/stream/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Visitor-Token': visitorToken },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  })
  return readSseResponse(response, onEvent, signal)
}
