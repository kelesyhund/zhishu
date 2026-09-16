import { effect, isReactive } from 'vue'
import { describe, expect, it } from 'vitest'

import { createStreamingAnswer } from '../../src/features/knowledge-detail/composables/useConversationChat'

describe('createStreamingAnswer', () => {
  it('notifies Vue when SSE content and references mutate', () => {
    const answer = createStreamingAnswer()
    let rendered = ''
    effect(() => { rendered = `${answer.content}:${answer.references.length}` })

    answer.content += '第一段'
    answer.references = [{ document_name: 'manual.txt', content: '证据', similarity: 0.92 }]

    expect(isReactive(answer)).toBe(true)
    expect(rendered).toBe('第一段:1')
  })
})
