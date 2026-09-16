import client from './client'

export interface KnowledgeBase {
  id: number
  name: string
  description: string
  document_count: number
  created_at: string
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
