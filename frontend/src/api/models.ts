import client from './client'

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
