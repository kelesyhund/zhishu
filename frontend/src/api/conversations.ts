import client, { csrfToken } from './client'
import { isRecord } from './common'
import type { AgentRunTrace } from './retrieval'
import { readSseResponse, type SseEventHandler } from './sse'

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
  onEvent: SseEventHandler,
  signal?: AbortSignal,
) {
  const response = await fetch(`/api/knowledge-bases/${id}/chat/stream/`, {
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
  if (!response.ok || !response.body) {
    const body: unknown = await response.json().catch(() => null)
    if (isRecord(body) && isRecord(body.data) && typeof body.data.conversation_id === 'number') {
      onEvent('meta', body.data)
    }
    const message = isRecord(body) && typeof body.message === 'string' ? body.message : '问答请求失败'
    throw new Error(message)
  }
  return readSseResponse(response, onEvent, signal)
}
