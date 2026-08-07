/**
 * Realtime client: one WebSocket per (workspace, token) with auto-reconnect and
 * a type-based subscription registry.
 *
 *   useRealtime('message.created', (data) => { ... })
 *   useRealtime('*', handler)          // all events
 *   sendRealtime({ type: 'typing', conversation_id, is_typing: true })
 */

import { useEffect } from 'react'

import { refreshSession } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

export interface RealtimeMessage {
  type: string
  data: Record<string, unknown>
}

type Handler = (data: Record<string, unknown>, message: RealtimeMessage) => void

const handlers = new Map<string, Set<Handler>>()

let socket: WebSocket | null = null
let socketKey = ''
let reconnectAttempt = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pingTimer: ReturnType<typeof setInterval> | null = null

function wsUrl(workspaceId: string, token: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || window.location.origin
  const url = new URL('/ws/app', base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('workspace_id', workspaceId)
  url.searchParams.set('token', token)
  return url.toString()
}

function dispatch(message: RealtimeMessage) {
  for (const key of [message.type, '*']) {
    handlers.get(key)?.forEach((handler) => {
      try {
        handler(message.data, message)
      } catch (error) {
        console.error('[stept] realtime handler failed', error)
      }
    })
  }
}

function connect() {
  const { workspaceId, accessToken } = useAuthStore.getState()
  if (!workspaceId || !accessToken) return
  const key = `${workspaceId}:${accessToken}`
  if (socket && socketKey === key && socket.readyState <= WebSocket.OPEN) return

  disconnect()
  socketKey = key
  socket = new WebSocket(wsUrl(workspaceId, accessToken))

  socket.onopen = () => {
    reconnectAttempt = 0
    pingTimer = setInterval(() => sendRealtime({ type: 'ping' }), 25_000)
  }
  socket.onmessage = (event) => {
    try {
      dispatch(JSON.parse(event.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  socket.onclose = (event) => {
    if (pingTimer) clearInterval(pingTimer)
    pingTimer = null
    if (handlers.size === 0) return
    const delay = Math.min(30_000, 1000 * 2 ** reconnectAttempt++)
    if (event.code === 4401) {
      // Token rejected (expired while the tab was idle): renew it first, then
      // reconnect — retrying with the same dead token would loop forever.
      socketKey = ''
      reconnectTimer = setTimeout(() => {
        void refreshSession().then(() => connect())
      }, delay)
      return
    }
    reconnectTimer = setTimeout(connect, delay)
  }
  socket.onerror = () => socket?.close()
}

function disconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (pingTimer) clearInterval(pingTimer)
  reconnectTimer = null
  pingTimer = null
  if (socket) {
    socket.onclose = null
    socket.close()
    socket = null
  }
}

export function sendRealtime(message: Record<string, unknown>) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
}

export function useRealtime(type: string, handler: Handler) {
  useEffect(() => {
    let set = handlers.get(type)
    if (!set) {
      set = new Set()
      handlers.set(type, set)
    }
    set.add(handler)
    connect()
    return () => {
      set.delete(handler)
      if (set.size === 0) handlers.delete(type)
      if (handlers.size === 0) disconnect()
    }
  }, [type, handler])
}

/** Reconnect with fresh credentials (workspace switch / login). */
export function reconnectRealtime() {
  socketKey = ''
  if (handlers.size > 0) connect()
}
