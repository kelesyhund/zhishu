import axios from 'axios'

export type RequestErrorKind =
  | 'cancelled'
  | 'timeout'
  | 'network'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'rate_limited'
  | 'server'
  | 'validation'
  | 'unknown'

export interface RequestErrorInfo {
  kind: RequestErrorKind
  message: string
  status?: number
  requestId?: string
}

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export function unwrapEnvelope<T>(value: unknown): T {
  if (!value || typeof value !== 'object' || !('data' in value)) {
    throw new Error('后端响应格式无效')
  }
  const envelope = value as Partial<ApiEnvelope<T>>
  if (typeof envelope.code !== 'number' || typeof envelope.message !== 'string') {
    throw new Error('后端响应格式无效')
  }
  return envelope.data as T
}

const client = axios.create({
  baseURL: '/api',
  timeout: 120000,
  withCredentials: true,
  xsrfCookieName: 'csrftoken',
  xsrfHeaderName: 'X-CSRFToken',
})

client.interceptors.request.use((config) => {
  const workspaceId = localStorage.getItem('active_workspace_id')
  const url = config.url || ''
  if (workspaceId && !url.startsWith('/public/') && !url.startsWith('/v1/')) {
    config.headers['X-Workspace-ID'] = workspaceId
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('active_workspace_id')
      const publicPath = location.pathname.startsWith('/share/') || location.pathname.startsWith('/invite/') || ['/reset-password','/verify-email'].includes(location.pathname)
      if (!publicPath && location.pathname !== '/login') location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export function requestErrorInfo(error: unknown): RequestErrorInfo {
  if (axios.isAxiosError(error)) {
    if (error.code === 'ERR_CANCELED') return { kind: 'cancelled', message: '请求已取消' }
    const data = error.response?.data
    const responseMessage =
      data && typeof data === 'object' && 'message' in data && typeof data.message === 'string'
        ? data.message
        : ''
    const status = error.response?.status
    const headers = error.response?.headers
    const headerRequestId = headers && typeof headers.get === 'function'
      ? headers.get('x-request-id')
      : headers?.['x-request-id']
    const bodyRequestId =
      data && typeof data === 'object' && 'request_id' in data && typeof data.request_id === 'string'
        ? data.request_id
        : undefined
    const requestId = typeof headerRequestId === 'string' ? headerRequestId : bodyRequestId
    if (error.code === 'ECONNABORTED') return { kind: 'timeout', message: '请求超时，请稍后重试', status, requestId }
    if (!error.response) return { kind: 'network', message: '无法连接后端服务，请确认后端已经启动', requestId }
    const responseStatus = error.response.status
    if (responseStatus === 401) return { kind: 'unauthorized', message: responseMessage || '登录状态已失效，请重新登录', status: responseStatus, requestId }
    if (responseStatus === 403) return { kind: 'forbidden', message: responseMessage || '没有权限执行此操作', status: responseStatus, requestId }
    if (responseStatus === 404) return { kind: 'not_found', message: responseMessage || '请求的资源不存在', status: responseStatus, requestId }
    if (responseStatus === 409) return { kind: 'conflict', message: responseMessage || '资源状态已发生变化，请刷新后重试', status: responseStatus, requestId }
    if (responseStatus === 429) return { kind: 'rate_limited', message: responseMessage || '请求过于频繁，请稍后重试', status: responseStatus, requestId }
    if (responseStatus >= 500) return { kind: 'server', message: '服务器暂时不可用，请稍后重试', status: responseStatus, requestId }
    return { kind: 'validation', message: responseMessage || `请求失败（${responseStatus}）`, status: responseStatus, requestId }
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    return { kind: 'cancelled', message: '请求已取消' }
  }
  return { kind: 'unknown', message: error instanceof Error ? error.message : '操作失败' }
}

export function errorMessage(error: unknown): string {
  return requestErrorInfo(error).message
}

export function errorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined
}

export default client

export function csrfToken(): string {
  const entry = document.cookie.split('; ').find((item) => item.startsWith('csrftoken='))
  return entry ? decodeURIComponent(entry.slice('csrftoken='.length)) : ''
}
