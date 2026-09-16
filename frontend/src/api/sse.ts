export interface SseEvent {
  event: string
  data: unknown
}

export type SseEventHandler = (event: string, data: unknown) => void

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError'
}

export function parseSseBlock(block: string): SseEvent | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(':')) continue
    if (rawLine.startsWith('event:')) event = rawLine.slice(6).trim()
    else if (rawLine.startsWith('data:')) dataLines.push(rawLine.slice(5).trimStart())
  }
  if (!dataLines.length) return null
  const rawData = dataLines.join('\n')
  try {
    return { event, data: JSON.parse(rawData) as unknown }
  } catch {
    throw new Error('流式响应格式无效')
  }
}

export async function readSseResponse(
  response: Response,
  onEvent: SseEventHandler,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.ok || !response.body) {
    const body: unknown = await response.json().catch(() => null)
    const message =
      typeof body === 'object' && body !== null && 'message' in body && typeof body.message === 'string'
        ? body.message
        : '问答请求失败'
    throw new Error(response.status >= 500 ? '模型服务暂时不可用，请稍后重试' : message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const abort = () => void reader.cancel().catch(() => undefined)
  signal?.addEventListener('abort', abort, { once: true })
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError')
      const { done, value } = await reader.read()
      if (signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError')
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        const parsed = parseSseBlock(block)
        if (parsed) onEvent(parsed.event, parsed.data)
      }
      if (done) break
    }
    if (buffer.trim()) {
      const parsed = parseSseBlock(buffer)
      if (parsed) onEvent(parsed.event, parsed.data)
    }
  } finally {
    signal?.removeEventListener('abort', abort)
    reader.releaseLock()
  }
}
