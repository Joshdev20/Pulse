import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken } from '../lib/api'
import { useAuth } from './AuthContext'
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
const BASE_BACKOFF_MS = 400

/**
 * Heartbeat cadence, and how long an unanswered ping is tolerated.
 *
 * A WebSocket over a mobile network or a suspended device commonly goes
 * half-open: the socket still reports `OPEN` and `onclose` never fires, so a
 * reconnect that only watches for `close` leaves the client silently receiving
 * nothing. The heartbeat is what notices: a ping that goes unanswered for
 * `LIVENESS_TIMEOUT_MS` (generous next to a normal sub-second round trip) means
 * the socket is dead, and it is closed so the reconnect path runs.
 */
const HEARTBEAT_MS = 10_000
const LIVENESS_TIMEOUT_MS = 15_000

/** Reconnect after a drop gets a little random jitter, so a server restart
 *  does not bring every client back in one synchronised wave. */
function jitter(ms: number): number {
  return Math.round(ms * (0.7 + Math.random() * 0.6))
}

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
  // The socket authenticates with whatever token was in the slot when it
  // opened, so a switch to another account has to tear it down and reconnect;
  // otherwise the previous user's messages keep arriving on this page.
  const { sessionEpoch } = useAuth()

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

    // Liveness bookkeeping. `lastSeen` is stamped on every frame from the
    // server; the heartbeat closes a socket that has gone quiet for longer than
    // the liveness window, which is the half-open case a `close` event misses.
    let lastSeen = Date.now()
    let pongPending = false
    const probeTimers = new Set<number>()

    const connect = () => {
      let socket: WebSocket
      try {
        socket = new WebSocket(socketUrl())
      } catch {
        scheduleReconnect()
        return
      }
      socketRef.current = socket
      lastSeen = Date.now()
      pongPending = false

      socket.onopen = () => {
        // The token travels in the first frame, never the URL, so it stays out
        // of access logs and browser history.
        socket.send(JSON.stringify({ type: 'auth', token }))
      }

      socket.onmessage = (raw) => {
        lastSeen = Date.now()
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
        // Any frame is proof of life; a `pong` is the answer to our ping.
        if (event.type === 'pong') pongPending = false
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

    const scheduleReconnect = (immediate = false) => {
      if (closedByUs.current) return
      if (reconnectTimer.current !== null) {
        // A recovery signal (the network came back) outranks a pending timer:
        // cancel the wait and reconnect now instead of serving out the backoff.
        if (!immediate) return
        window.clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      const delay = immediate ? 0 : jitter(Math.min(BASE_BACKOFF_MS * 2 ** attemptsRef.current, MAX_BACKOFF_MS))
      attemptsRef.current += 1
      reconnectTimer.current = window.setTimeout(() => {
        reconnectTimer.current = null
        connect()
      }, delay)
    }

    /**
     * Reconnect at once when the conditions that break a socket are undone.
     *
     * A phone that was asleep, a laptop that changed networks or a tab that was
     * backgrounded all leave a socket that is dead but not yet closed. Waiting
     * out the backoff would keep alerts slow for up to 15s after the user is
     * back, so these events drive an immediate retry.
     *
     * A socket that still looks open is probed with a ping before it is
     * discarded: on a flaky network `focus` can fire while the socket is fine,
     * and dropping a healthy socket would lose any event in flight. The ping is
     * answered by the server's `pong`, which stamps `lastSeen`.
     */
    const recover = () => {
      if (closedByUs.current) return
      const socket = socketRef.current
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'ping' }))
        const probe = window.setTimeout(() => {
          probeTimers.delete(probe)
          if (closedByUs.current) return
          // A live socket answered the ping and stamped `lastSeen`; anything
          // older than the liveness window means the ping was swallowed.
          if (socketRef.current !== socket || Date.now() - lastSeen > LIVENESS_TIMEOUT_MS) return
          attemptsRef.current = 0
          socket.close()
        }, LIVENESS_TIMEOUT_MS)
        probeTimers.add(probe)
        return
      }
      attemptsRef.current = 0
      scheduleReconnect(true)
    }

    const onVisibility = () => {
      if (document.visibilityState === 'visible') recover()
    }

    connect()

    // The heartbeat doubles as liveness detection and as the keep-alive that
    // stops an idle proxy from closing a quiet connection.
    const heartbeat = window.setInterval(() => {
      const socket = socketRef.current
      if (socket?.readyState !== WebSocket.OPEN) return
      // A ping that is still unanswered after a full liveness window means the
      // socket is half-open: closing it makes `onclose` schedule the reconnect.
      if (pongPending && Date.now() - lastSeen > LIVENESS_TIMEOUT_MS) {
        socket.close()
        return
      }
      pongPending = true
      socket.send(JSON.stringify({ type: 'ping' }))
    }, HEARTBEAT_MS)

    window.addEventListener('online', recover)
    window.addEventListener('focus', recover)
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      closedByUs.current = true
      window.clearInterval(heartbeat)
      for (const probe of probeTimers) window.clearTimeout(probe)
      probeTimers.clear()
      window.removeEventListener('online', recover)
      window.removeEventListener('focus', recover)
      document.removeEventListener('visibilitychange', onVisibility)
      if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current)
      reconnectTimer.current = null
      socketRef.current?.close()
      socketRef.current = null
      setConnected(false)
    }
  }, [emit, sessionEpoch])

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
  const { user } = useAuth()

  const refresh = useCallback(() => {
    // Nothing to count without a session, and the request would only 401.
    if (!user) {
      setCount(0)
      return
    }
    api
      .messagesUnreadCount()
      .then((result) => setCount(result.count))
      .catch(() => undefined)
  }, [user])

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
  const { user } = useAuth()

  const refresh = useCallback(() => {
    // As above: a visitor has no unread anything.
    if (!user) {
      setCount(0)
      return
    }
    api
      .unreadCount()
      .then((result) => setCount(result.count))
      .catch(() => undefined)
  }, [user])

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
