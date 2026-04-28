export class SSEClient {
  constructor(url, handlers) {
    this.url = url
    this.handlers = handlers
    this.abortController = null
    this.sessionId = null
    this.conversationId = null
  }

  async connect(payload) {
    this.abortController = new AbortController()
    const resp = await fetch(this.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: this.abortController.signal,
    })

    this.sessionId = resp.headers.get('X-Session-Id')
    this.conversationId = resp.headers.get('X-Conversation-Id')

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    // 跨 chunk 持久化——SSE 规范用空行做事件边界，event: 和 data: 可能被 TCP
    // 拆到两个 chunk。早期版本在每次 while 迭代里重置 eventType，导致跨 chunk 的
    // data 行落到 'message' handler，被 ?.() 静默吞掉。
    let eventType = 'message'

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const raw of lines) {
        const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
        if (line === '') {
          // 空行 = 事件边界，重置 eventType
          eventType = 'message'
          continue
        }
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          try {
            const data = JSON.parse(line.slice(5).trim())
            this.handlers[eventType]?.(data)
          } catch (_) {}
        }
      }
    }
  }

  abort() {
    this.abortController?.abort()
  }
}
