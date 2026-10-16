import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, api, setToken, getToken } from '../lib/api'
import { clearMediaCache } from '../lib/mediaCache'
import type { User } from '../lib/types'

// A failed session check is retried before the user is shown as signed out, so
// a rate limit or a dropped connection does not end their session.
const AUTH_RETRY_LIMIT = 3
const AUTH_RETRY_BASE_MS = 400

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

interface AuthValue {
  user: User | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  register: (data: {
    username: string
    email: string
    display_name: string
    password: string
  }) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
  setUser: (user: User) => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUserState(null)
      return
    }
    for (let attempt = 0; ; attempt++) {
      try {
        setUserState(await api.me())
        return
      } catch (error) {
        // Only a rejected token ends the session. A rate limit, a dropped
        // connection or a 5xx is transient, so retry with backoff instead of
        // discarding credentials the server never invalidated.
        if (error instanceof ApiError && error.status === 401) {
          clearMediaCache()
          setToken(null)
          setUserState(null)
          return
        }
        if (attempt >= AUTH_RETRY_LIMIT) {
          setUserState(null)
          return
        }
        await delay(AUTH_RETRY_BASE_MS * 2 ** attempt)
      }
    }
  }, [])

  useEffect(() => {
    refresh().finally(() => setLoading(false))
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    const { access_token } = await api.login(username, password)
    // The previous session's media is keyed only by path, so it has to go
    // before the new account renders anything.
    clearMediaCache()
    setToken(access_token)
    setUserState(await api.me())
  }, [])

  const register = useCallback(
    async (data: { username: string; email: string; display_name: string; password: string }) => {
      const { access_token } = await api.register(data)
      clearMediaCache()
      setToken(access_token)
      setUserState(await api.me())
    },
    [],
  )

  const logout = useCallback(() => {
    clearMediaCache()
    setToken(null)
    setUserState(null)
  }, [])

  const value = useMemo<AuthValue>(
    () => ({ user, loading, login, register, logout, refresh, setUser: setUserState }),
    [user, loading, login, register, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
