import { describe, expect, it } from 'vitest'

import { conversationIdFromQuery, withConversationQuery } from '../../src/features/knowledge-detail/conversationRoute'

describe('conversation route state', () => {
  it('accepts only positive integer conversation ids', () => {
    expect(conversationIdFromQuery({ conversation: '12' })).toBe(12)
    expect(conversationIdFromQuery({ conversation: '0' })).toBeNull()
    expect(conversationIdFromQuery({ conversation: 'abc' })).toBeNull()
    expect(conversationIdFromQuery({ conversation: ['7', '8'] })).toBe(7)
  })

  it('updates or removes only the conversation query', () => {
    expect(withConversationQuery({ tab: 'docs' }, 9)).toEqual({ tab: 'docs', conversation: '9' })
    expect(withConversationQuery({ tab: 'docs', conversation: '9' }, null)).toEqual({ tab: 'docs' })
  })
})
