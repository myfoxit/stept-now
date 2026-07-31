/**
 * Reconnecting visitor websocket (`/ws/widget?token=`).
 *
 * The server auto-joins every conversation the contact owns on connect, so we
 * only need to explicitly `subscribe` to conversations created mid-session
 * (and re-send those on reconnect).
 */

import type { RealtimeMessage } from '../types'

export class WidgetSocket {
  private url: string
  private onMessage: (msg: RealtimeMessage) => void
  private ws: WebSocket | null = null
  private closed = false
  private retry = 0
  private subscribed = new Set<string>()
  private pingTimer: ReturnType<typeof setInterval> | null = null

  constructor(url: string, onMessage: (msg: RealtimeMessage) => void) {
    this.url = url
    this.onMessage = onMessage
  }

  connect(): void {
    if (this.closed || typeof WebSocket === 'undefined') return
    let ws: WebSocket
    try {
      ws = new WebSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      for (const id of this.subscribed) this.send({ type: 'subscribe', conversation_id: id })
      this.pingTimer = setInterval(() => this.send({ type: 'ping' }), 25_000)
    }
    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data as string) as RealtimeMessage
        if (parsed && typeof parsed.type === 'string') this.onMessage(parsed)
      } catch {
        /* ignore malformed frames */
      }
    }
    ws.onclose = () => {
      this.clearPing()
      this.ws = null
      this.scheduleReconnect()
    }
    ws.onerror = () => ws.close()
  }

  subscribe(conversationId: string): void {
    this.subscribed.add(conversationId)
    this.send({ type: 'subscribe', conversation_id: conversationId })
  }

  typing(conversationId: string, isTyping: boolean): void {
    this.send({ type: 'typing', conversation_id: conversationId, is_typing: isTyping })
  }

  close(): void {
    this.closed = true
    this.clearPing()
    this.ws?.close()
    this.ws = null
  }

  private send(obj: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj))
    }
  }

  private scheduleReconnect(): void {
    if (this.closed) return
    const delay = Math.min(30_000, 1000 * 2 ** this.retry++)
    setTimeout(() => this.connect(), delay)
  }

  private clearPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.pingTimer = null
  }
}
