export class SSEClient {
  constructor(url, handlers) {
    this.url = url
    this.handlers = handlers  // { step, token, chart, interrupt, done, error }
    this.abortController = null
  }

  async connect(payload) {
    this.abortController = new AbortController()
    const resp = await fetch(this.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: this.abortController.signal,
    })

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      let eventType = 'message'
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          try {
            const data = JSON.parse(line.slice(5).trim())
            this.handlers[eventType]?.(data)
          } catch (_) {}
          eventType = 'message'
        }
      }
    }
  }

  abort() {
    this.abortController?.abort()
  }
}
