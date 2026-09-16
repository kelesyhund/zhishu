import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatMessageList from '../../src/features/knowledge-detail/components/ChatMessageList.vue'

describe('ChatMessageList', () => {
  it('renders citations and sanitizes hostile assistant markdown', () => {
    const wrapper = mount(ChatMessageList, {
      props: {
        messages: [{
          role: 'assistant',
          content: '<img src=x onerror="window.__xss=true"><script>window.__xss=true</script>**安全答案**',
          references: [{ document_name: '<img onerror=alert(1)>', content: '可信正文', similarity: 0.91 }],
        }],
      },
    })
    expect(wrapper.html()).toContain('<strong>安全答案</strong>')
    expect(wrapper.get('.markdown-body').html()).not.toContain('onerror')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.get('[data-testid="chat-references"]').text()).toContain('可信正文')
  })
})
