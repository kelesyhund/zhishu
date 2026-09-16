import type { AgentRunTrace, ReferenceItem } from '../../api'

export interface ChatMessage {
  id?: number
  role: 'user' | 'assistant'
  content: string
  references: ReferenceItem[]
  agentTrace?: AgentRunTrace | null
  created_at?: string
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isReferenceItems(value: unknown): value is ReferenceItem[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        isRecord(item) &&
        typeof item.document_name === 'string' &&
        typeof item.content === 'string' &&
        typeof item.similarity === 'number',
    )
  )
}
