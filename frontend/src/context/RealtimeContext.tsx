import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken } from '../lib/api'
import type { Conversation, Message, RealtimeNotification } from '../lib/types'

export interface RealtimeEvent {
  type: 'notification' | 'message' | 'typing' | 'ready' | 'pong'
  notification?: RealtimeNotification
  message?: Message
  conversation_id?: number
  user_id?: number
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

/** Resolve the websocket URL for the current origin, honouring https -> wss. */
function socketUrl(): string {
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
