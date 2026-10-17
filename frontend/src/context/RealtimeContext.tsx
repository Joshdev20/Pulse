import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken } from '../lib/api'
import type { Conversation, Message, RealtimeNotification } from '../lib/types'

/** The actor behind a notification, enough to render a popup without a fetch. */
export interface NotificationActor {
  id: number
  username: string
  display_name: string
  avatar_color: string
  avatar_url: string | null
}

export interface RealtimeEvent {
  type: 'notification' | 'message' | 'message_like' | 'typing' | 'presence' | 'ready' | 'pong'
  notification?: RealtimeNotification
  /** Present on `notification` frames so a popup can name who acted. */
  actor?: NotificationActor | null
  message?: Message
  conversation_id?: number
  user_id?: number
  /** Present on `message_like` frames. */
  message_id?: number
  like_count?: number
  /** Present on `presence` frames: a partner came online or went offline. */
  is_online?: boolean
  last_seen_at?: string | null
}

type Listener = (event: RealtimeEvent) => void

interface RealtimeValue {
  connected: boolean
  subscribe: (listener: Listener) => () => void
  /** Ask the server to relay a typing indicator for this conversation. */
  sendTyping: (conversationId: number) => void
}

const RealtimeContext = createContext<RealtimeValue | null>(null)

const MAX_BACKOFF_MS = 15_000
const BASE_BACKOFF_MS = 800

/**
 * Resolve the websocket URL.
 *
 * With `VITE_API_BASE` set the socket points at the API host (honouring
 * https -> wss); otherwise it stays on the current origin, which is what the
 * Vite dev proxy and a same-origin production deploy both expect.
 */
function socketUrl(): string {
  const base = import.meta.env.VITE_API_BASE
  if (base) {
    const url = new URL(base)
    const scheme = url.protocol === 'https:' ? 'wss' : 'ws'
    return `${scheme}://${url.host}/api/ws`
  }
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/api/ws`
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)
  const listenersRef = useRef(new Set<Listener>())
  const attemptsRef = useRef(0)
  const reconnectTimer = useRef<number | null>(null)
  const closedByUs = useRef(false)

  const emit = useCallback((event: RealtimeEvent) => {
    for (const listener of listenersRef.current) listener(event)
  }, [])

  const subscribe = useCallback((listener: Listener) => {
    listenersRef.current.add(listener)
    return () => {
      listenersRef.current.delete(listener)
    }
  }, [])

  const sendTyping = useCallback((conversationId: number) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'typing', conversation_id: conversationId }))
    }
  }, [])

  useEffect(() => {
    const token = getToken()
    if (!token) {
      setConnected(false)
      return
    }

    closedByUs.current = false

    const connect = () => {
      let socket: WebSocket
      try {
        socket = new WebSocket(socketUrl())
      } catch {
        scheduleReconnect()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        // The token travels in the first frame, never the URL, so it stays out
        // of access logs and browser history.
        socket.send(JSON.stringify({ type: 'auth', token }))
      }

      socket.onmessage = (raw) => {
        let event: RealtimeEvent
        try {
          event = JSON.parse(raw.data)
        } catch {
          return
        }
        if (event.type === 'ready') {
          attemptsRef.current = 0
          setConnected(true)
          return
        }
        emit(event)
      }

      socket.onclose = () => {
        // A socket that is no longer the current one is a leftover from a
        // remount (React StrictMode mounts effects twice in development). Its
        // close must not clear the live socket's ref, report a disconnect or
        // schedule a reconnect, or the app ends up with two authenticated
        // sockets and every event arrives twice.
        if (socketRef.current !== socket) return
        setConnected(false)
        socketRef.current = null
        if (!closedByUs.current) scheduleReconnect()
      }

      socket.onerror = () => {
        // onclose fires next and owns the retry; closing here avoids a double.
        socket.close()
      }
    }

    const scheduleReconnect = () => {
      if (closedByUs.current || reconnectTimer.current !== null) return
      const delay = Math.min(BASE_BACKOFF_MS * 2 ** attemptsRef.current, MAX_BACKOFF_MS)
      attemptsRef.current += 1
      reconnectTimer.current = window.setTimeout(() => {
        reconnectTimer.current = null
        connect()
      }, delay)
    }

    connect()

    // Keep the socket warm through proxies that idle out quiet connections.
    const heartbeat = window.setInterval(() => {
      const socket = socketRef.current
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'ping' }))
      }
    }, 25_000)

    return () => {
      closedByUs.current = true
      window.clearInterval(heartbeat)
      if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current)
      reconnectTimer.current = null
      socketRef.current?.close()
      socketRef.current = null
      setConnected(false)
    }
  }, [emit])

  const value = useMemo<RealtimeValue>(
    () => ({ connected, subscribe, sendTyping }),
    [connected, subscribe, sendTyping],
  )

  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>
}

export function useRealtime(): RealtimeValue {
  const context = useContext(RealtimeContext)
  if (!context) throw new Error('useRealtime must be used inside a RealtimeProvider')
  return context
}

/** Subscribe to realtime events for the lifetime of the component. */
export function useRealtimeEvent(listener: Listener, deps: unknown[] = []) {
  const { subscribe } = useRealtime()
  const listenerRef = useRef(listener)
  listenerRef.current = listener

  useEffect(() => {
    return subscribe((event) => listenerRef.current(event))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscribe, ...deps])
}

/** Total unread direct messages, refreshed on realtime message events. */
export function useUnreadMessages(): { count: number; refresh: () => void } {
  const [count, setCount] = useState(0)
  const { subscribe } = useRealtime()

  const refresh = useCallback(() => {
    api
      .messagesUnreadCount()
      .then((result) => setCount(result.count))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message') refresh()
      }),
    [subscribe, refresh],
  )

  return { count, refresh }
}

/** Unread notification count, incremented live as events arrive. */
export function useUnreadNotifications(): { count: number; refresh: () => void } {
  const [count, setCount] = useState(0)
  const { subscribe } = useRealtime()

  const refresh = useCallback(() => {
    api
      .unreadCount()
      .then((result) => setCount(result.count))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'notification') setCount((current) => current + 1)
      }),
    [subscribe],
  )

  return { count, refresh }
}

export type { Conversation, Message }
