import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, setToken, getToken } from '../lib/api'
import type { User } from '../lib/types'

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
    try {
      setUserState(await api.me())
    } catch {
      setToken(null)
      setUserState(null)
    }
  }, [])

  useEffect(() => {
    refresh().finally(() => setLoading(false))
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    const { access_token } = await api.login(username, password)
    setToken(access_token)
    setUserState(await api.me())
  }, [])

  const register = useCallback(
    async (data: { username: string; email: string; display_name: string; password: string }) => {
      const { access_token } = await api.register(data)
      setToken(access_token)
      setUserState(await api.me())
    },
    [],
  )

  const logout = useCallback(() => {
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
