import { describe, expect, it, vi } from 'vitest'

import { isAbortError, parseSseBlock, readSseResponse } from '../../src/api/sse'

describe('SSE helpers', () => {
  it('parses named and multiline events', () => {
    expect(parseSseBlock('event: content\ndata: {"content":\ndata: "hello"}')).toEqual({
      event: 'content',
      data: { content: 'hello' },
    })
  })

  it('ignores comments and rejects invalid JSON', () => {
    expect(parseSseBlock(': heartbeat')).toBeNull()
    expect(() => parseSseBlock('data: not-json')).toThrow('流式响应格式无效')
  })

  it('reads complete and trailing events', async () => {
    const encoder = new TextEncoder()
    const response = new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('event: meta\ndata: {"conversation_id":1}\n\n'))
          controller.enqueue(encoder.encode('event: content\ndata: {"content":"答案"}'))
          controller.close()
        },
      }),
    )
    const handler = vi.fn()
    await readSseResponse(response, handler)
    expect(handler).toHaveBeenNthCalledWith(1, 'meta', { conversation_id: 1 })
    expect(handler).toHaveBeenNthCalledWith(2, 'content', { content: '答案' })
  })

  it('recognizes AbortError', () => {
    expect(isAbortError(new DOMException('aborted', 'AbortError'))).toBe(true)
    expect(isAbortError(new Error('failed'))).toBe(false)
  })

  it('cancels an active stream without reporting a server failure', async () => {
    const controller = new AbortController()
    const response = new Response(new ReadableStream({ start() { /* remains open until cancellation */ } }))
    const reading = readSseResponse(response, vi.fn(), controller.signal)
    controller.abort()
    await expect(reading).rejects.toMatchObject({ name: 'AbortError' })
  })
})
