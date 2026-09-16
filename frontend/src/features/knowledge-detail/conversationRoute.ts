import type { LocationQuery, LocationQueryRaw } from 'vue-router'

export function conversationIdFromQuery(query: LocationQuery): number | null {
  const raw = Array.isArray(query.conversation) ? query.conversation[0] : query.conversation
  if (!raw) return null
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 ? value : null
}

export function withConversationQuery(
  query: LocationQuery,
  conversationId: number | null,
): LocationQueryRaw {
  const next: LocationQueryRaw = { ...query }
  if (conversationId === null) delete next.conversation
  else next.conversation = String(conversationId)
  return next
}
