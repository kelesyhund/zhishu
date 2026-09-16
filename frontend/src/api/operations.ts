import client from './client'
import type { PageResult } from './common'
import type { AuditEventItem } from './organizations'

export interface DashboardOverview {
  range: '7d' | '30d'
  counts: Record<string, number>
  application_calls: { total: number; success: number; success_rate: number; average_latency_ms: number; p95_latency_ms: number; no_answer: number }
  document_processing: { total: number; success: number; success_rate: number }
  task_counts: { active: number; failure: number }
  trend: { date: string; total: number; success: number }[]
}

export interface AttentionItem { type: string; title: string; description: string; resource_id: number }
export async function getDashboardOverview(range: '7d' | '30d' = '7d'): Promise<DashboardOverview> {
  return (await client.get('/dashboard/overview/', { params: { range } })).data.data
}
export async function getDashboardAttention(): Promise<{ items: AttentionItem[]; total: number }> {
  return (await client.get('/dashboard/attention-items/')).data.data
}
export async function getDashboardActivity(): Promise<PageResult<AuditEventItem>> {
  return (await client.get('/dashboard/activity/')).data.data
}

export interface GlobalTaskItem {
  id: number; document_id: number; document_name: string; knowledge_base_id: number | null
  knowledge_base_name: string; task_type: string; status: string; progress: number
  current_stage: string; attempt_count: number; error_message: string; created_at: string; updated_at: string
}
export async function listGlobalTasks(params: Record<string, string | number> = {}): Promise<PageResult<GlobalTaskItem>> {
  return (await client.get('/processing-tasks/', { params })).data.data
}
export async function retryGlobalTask(id: number): Promise<GlobalTaskItem> {
  return (await client.post(`/processing-tasks/${id}/retry/`)).data.data
}
export async function cancelGlobalTask(id: number): Promise<GlobalTaskItem> {
  return (await client.post(`/processing-tasks/${id}/cancel/`)).data.data
}

export interface EmbeddingSpaceItem {
  id: number; signature: string; model_name: string; revision: number; dimension: number
  distance_metric: 'COSINE'; status: 'DISCOVERED' | 'BUILDING' | 'READY' | 'DEGRADED' | 'RETIRED'
  indexed: boolean; vector_count: number; created_at: string; updated_at: string
}
export interface VectorMigrationItem {
  id: number; space: EmbeddingSpaceItem; status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILURE' | 'CANCEL_REQUESTED' | 'CANCELLED'
  total_count: number; succeeded_count: number; failed_count: number; skipped_count: number
  cursor_id: number; batch_size: number; progress: number; error_message: string
  created_at: string; started_at: string | null; finished_at: string | null; updated_at: string
}
export interface VectorIndexStatus {
  write_mode: 'LEGACY' | 'DUAL' | 'PGVECTOR'; read_mode: 'LEGACY' | 'SHADOW' | 'PGVECTOR'
  search_mode: 'EXACT' | 'HNSW'; shadow_sample_rate: number; database_vendor: string
  pgvector_available: boolean; legacy_vector_count: number; pgvector_row_count: number
  covered_paragraph_count: number; coverage_ratio: number; spaces: EmbeddingSpaceItem[]
  recent_migrations: VectorMigrationItem[]
}
export async function getVectorIndexStatus(): Promise<VectorIndexStatus> {
  return (await client.get('/vector-index/status/')).data.data
}
export async function listVectorMigrations(page = 1): Promise<PageResult<VectorMigrationItem>> {
  return (await client.get('/vector-index/migrations/', { params: { page, page_size: 20 } })).data.data
}
export async function createVectorMigration(knowledgeBaseId: number, batchSize: number): Promise<VectorMigrationItem> {
  return (await client.post('/vector-index/migrations/', { knowledge_base_id: knowledgeBaseId, batch_size: batchSize })).data.data
}
export async function retryVectorMigration(id: number): Promise<VectorMigrationItem> {
  return (await client.post(`/vector-index/migrations/${id}/retry/`)).data.data
}
export async function cancelVectorMigration(id: number): Promise<VectorMigrationItem> {
  return (await client.post(`/vector-index/migrations/${id}/cancel/`)).data.data
}
