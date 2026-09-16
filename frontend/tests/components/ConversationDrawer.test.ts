import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConversationDrawer from '../../src/features/knowledge-detail/components/ConversationDrawer.vue'

describe('ConversationDrawer', () => {
  it('marks active conversation and emits selection', async () => {
    const wrapper = mount(ConversationDrawer, {
      props: {
        visible: true, page: 1, conversations: [{ id: 2, title: '故障排查', message_count: 2, created_at: '2026-01-01T00:00:00Z', last_message_at: null }],
        loading: false, pageSize: 20, total: 1, activeId: 2, renamingId: null, deletingId: null, sending: false,
        'onUpdate:visible': () => undefined, 'onUpdate:page': () => undefined,
      },
      global: {
        stubs: {
          'el-drawer': { template: '<section><slot name="header"/><slot/><slot name="footer"/></section>' },
          'el-button': { template: '<button><slot /></button>' },
          'el-empty': true,
          'el-pagination': true,
          'el-icon': true,
        },
      },
    })
    expect(wrapper.get('[data-testid="conversation-2"]').classes()).toContain('active')
    await wrapper.get('.conversation-select').trigger('click')
    expect(wrapper.emitted('select')).toEqual([[2]])
  })
})
