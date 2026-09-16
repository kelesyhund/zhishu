import client from './client'

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
