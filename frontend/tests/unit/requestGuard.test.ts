import { describe, expect, it } from 'vitest'

import { useRequestGuard } from '../../src/features/knowledge-detail/composables/useRequestGuard'

describe('request guard', () => {
  it('rejects responses from superseded requests', () => {
    const guard = useRequestGuard()
    const first = guard.next()
    const second = guard.next()
    expect(guard.current(first)).toBe(false)
    expect(guard.current(second)).toBe(true)
    guard.invalidate()
    expect(guard.current(second)).toBe(false)
  })
})
