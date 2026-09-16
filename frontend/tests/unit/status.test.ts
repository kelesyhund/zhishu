import { describe, expect, it } from 'vitest'

import { documentStatusTag, documentStatusText, taskStatusText } from '../../src/features/knowledge-detail/status'

describe('knowledge detail status mapping', () => {
  it('keeps backend enum values out of user-facing labels', () => {
    expect(documentStatusText('SUCCESS')).toBe('处理成功')
    expect(documentStatusTag('FAILURE')).toBe('danger')
    expect(taskStatusText('ENQUEUE_FAILED')).toBe('任务投递失败')
  })
})
