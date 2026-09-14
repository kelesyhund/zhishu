import axios from 'axios'

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

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data
    const responseMessage =
      data && typeof data === 'object' && 'message' in data && typeof data.message === 'string'
        ? data.message
        : ''
    if (responseMessage) return responseMessage
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
    if (!error.response) return '无法连接后端服务，请确认后端已经启动'
    if (error.response.status >= 500) {
      return '服务器暂时不可用；本地运行时请确认后端服务已经启动'
    }
    return `请求失败（${error.response.status}）`
  }
  return error instanceof Error ? error.message : '操作失败'
}

export function errorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined
}

export default client

export function csrfToken(): string {
  const entry = document.cookie.split('; ').find((item) => item.startsWith('csrftoken='))
  return entry ? decodeURIComponent(entry.slice('csrftoken='.length)) : ''
}
