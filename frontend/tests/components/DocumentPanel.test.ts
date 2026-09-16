import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DocumentPanel from '../../src/features/knowledge-detail/components/DocumentPanel.vue'
import type { DocumentItem } from '../../src/api'

const elementStubs = {
  'el-icon': { template: '<span><slot /></span>' },
  'el-tag': { template: '<span><slot /></span>' },
  'el-progress': { template: '<span />' },
  'el-button': { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
}

const document: DocumentItem = {
  id: 3, name: 'manual.md', status: 'SUCCESS', error_message: '', paragraph_count: 4,
  parent_chunk_count: 2, needs_reprocess: true, source_id: '', source_sha256: '', parser_type: 'MARKDOWN',
  chunk_strategy: 'PARENT_CHILD', parent_max_tokens: 1500, child_target_tokens: 400,
  child_overlap_tokens: 60, preserve_tables: true, preserve_code_blocks: true,
  parser_version: '1', chunker_version: '1', parsing_warnings: [], created_at: '',
}

describe('DocumentPanel', () => {
  it('shows empty state', () => {
    const wrapper = mount(DocumentPanel, { props: { knowledgeName: '测试库', documents: [], tasks: [], uploading: false, deletingId: null, reprocessingId: null, retryingTaskId: null, cancellingTaskId: null }, global: { stubs: elementStubs } })
    expect(wrapper.get('[data-testid="document-empty"]').text()).toContain('还没有文档')
  })

  it('shows status and emits paragraph action', async () => {
    const wrapper = mount(DocumentPanel, { props: { knowledgeName: '测试库', documents: [document], tasks: [], uploading: false, deletingId: null, reprocessingId: null, retryingTaskId: null, cancellingTaskId: null }, global: { stubs: elementStubs } })
    expect(wrapper.text()).toContain('处理成功')
    expect(wrapper.text()).toContain('向量需更新')
    const buttons = wrapper.findAll('button')
    await buttons.find((button) => button.text().includes('查看切片'))!.trigger('click')
    expect(wrapper.emitted('paragraphs')?.[0]).toEqual([document])
  })
})
