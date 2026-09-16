import { describe, expect, it } from 'vitest'

import { renderSafeMarkdown } from '../../src/utils/markdown'

describe('renderSafeMarkdown', () => {
  it('renders ordinary markdown', () => {
    const html = renderSafeMarkdown('## 标题\n\n**内容**')
    expect(html).toContain('<h2>标题</h2>')
    expect(html).toContain('<strong>内容</strong>')
  })

  it('removes executable html and dangerous URLs', () => {
    const html = renderSafeMarkdown(
      '<img src=x onerror="alert(1)"><script>alert(1)</script>[危险](javascript:alert(1))',
    )
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('javascript:')
  })

  it('hardens external links without changing local links', () => {
    const html = renderSafeMarkdown('[外部](https://example.com) [内部](/knowledge)')
    expect(html).toContain('href="https://example.com"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('href="/knowledge"')
  })
})
