import { effect, isReactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { createStreamingAnswer } from '../../src/features/knowledge-detail/composables/useConversationChat'

describe('createStreamingAnswer', () => {
  it('notifies Vue when SSE content and references mutate', () => {
    const answer = createStreamingAnswer()
    const render = vi.fn(() => `${answer.content}:${answer.references.length}`)
    effect(render)

    answer.content += '第一段'
    answer.references = [{ document_name: 'manual.txt', content: '证据', similarity: 0.92 }]

    expect(isReactive(answer)).toBe(true)
    expect(render).toHaveBeenLastCalledWith('第一段:1')
  })
})
