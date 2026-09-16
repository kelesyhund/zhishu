import axios from 'axios'
import { describe, expect, it } from 'vitest'

import { requestErrorInfo, unwrapEnvelope } from '../../src/api/client'

function axiosError(status: number, message = '', requestId = 'req-test') {
  return new axios.AxiosError(
    'request failed',
    'ERR_BAD_RESPONSE',
    undefined,
    undefined,
    {
      data: { message },
      status,
      statusText: '',
      headers: new axios.AxiosHeaders({ 'x-request-id': requestId }),
      config: { headers: new axios.AxiosHeaders() },
    },
  )
}

describe('requestErrorInfo', () => {
  it('unwraps the unified response envelope', () => {
    expect(unwrapEnvelope<{ id: number }>({ code: 200, message: 'success', data: { id: 7 } })).toEqual({ id: 7 })
    expect(() => unwrapEnvelope({ data: { id: 7 } })).toThrow('后端响应格式无效')
  })

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [409, 'conflict'],
    [429, 'rate_limited'],
    [500, 'server'],
  ] as const)('maps HTTP %s to %s', (status, kind) => {
    const result = requestErrorInfo(axiosError(status, '后端消息'))
    expect(result.kind).toBe(kind)
    expect(result.requestId).toBe('req-test')
  })

  it('does not expose a backend 500 message', () => {
    expect(requestErrorInfo(axiosError(500, 'database password leaked')).message).toBe(
      '服务器暂时不可用，请稍后重试',
    )
  })

  it('distinguishes cancellation from failures', () => {
    expect(requestErrorInfo(new DOMException('aborted', 'AbortError')).kind).toBe('cancelled')
  })

  it('distinguishes timeout and offline network failures', () => {
    expect(requestErrorInfo(new axios.AxiosError('timeout', 'ECONNABORTED')).kind).toBe('timeout')
    expect(requestErrorInfo(new axios.AxiosError('offline', 'ERR_NETWORK')).kind).toBe('network')
  })
})
