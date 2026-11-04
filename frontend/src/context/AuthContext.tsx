import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, api, setToken, getToken } from '../lib/api'
import { clearMediaCache } from '../lib/mediaCache'
import { clearCachedPushState } from '../lib/push'
import { clearLastUser, loadLastUser, saveLastUser } from '../lib/lastUser'
import {
  getActiveId,
  listAccounts,
  removeAccount,
  saveAccount,
  setActiveId,
  type SavedAccount,
} from '../lib/accounts'
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
  /** Every account saved on this device, in the order they were added. */
  accounts: SavedAccount[]
  /** The id of the account currently in use, or null when signed out. */
  activeAccountId: number | null
  /** Swap to another saved account. Resolves once its session is live. */
  switchAccount: (id: number) => Promise<void>
  /** Forget one saved account without necessarily signing out. */
  forgetAccount: (id: number) => void
  /**
   * Increments whenever the signed-in session changes (sign in, sign out,
   * switch). Realtime state keys on this so a socket authenticated as the
   * previous user is torn down rather than left delivering their events.
   */
  sessionEpoch: number
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  // A returning visitor is rendered from the cached profile of their last
  // session, so the app appears at once while `/auth/me` confirms the token in
  // the background. This is only a display copy of the viewer's own profile —
  // nothing is authorised from it, and a token the server rejects clears it.
  const [user, setUserState] = useState<User | null>(() => (getToken() ? loadLastUser() : null))
  // The loader is skipped only when there is a cached profile to show. A token
  // with no cached profile still waits for the check, otherwise `RequireAuth`
  // would see a null user and bounce a valid session to the sign-in screen.
  const [loading, setLoading] = useState(() => !(getToken() && loadLastUser()))
  const [accounts, setAccounts] = useState<SavedAccount[]>(() => listAccounts())
  const [activeAccountId, setActiveAccountIdState] = useState<number | null>(() => getActiveId())
  // Bumped on every session change. The realtime provider keys its socket on
  // this, so switching accounts tears the old socket down and authenticates a
  // new one instead of leaving the previous user's events arriving.
  const [sessionEpoch, setSessionEpoch] = useState(0)

  const syncAccounts = useCallback(() => {
    setAccounts(listAccounts())
    setActiveAccountIdState(getActiveId())
  }, [])

  const refresh = useCallback(async () => {
    if (!getToken()) {
      clearLastUser()
      setUserState(null)
      return
    }
    for (let attempt = 0; ; attempt++) {
      try {
        const me = await api.me()
        saveLastUser(me)
        setUserState(me)
        return
      } catch (error) {
        // Only a rejected token ends the session. A rate limit, a dropped
        // connection or a 5xx is transient, so retry with backoff instead of
        // discarding credentials the server never invalidated.
        if (error instanceof ApiError && error.status === 401) {
          clearMediaCache()
          clearCachedPushState()
          clearLastUser()
          setToken(null)
          setUserState(null)
          return
        }
        if (attempt >= AUTH_RETRY_LIMIT) {
          // Out of retries on a transient failure. Whatever profile is already
          // on screen stays: the token is still held, so this is a blip rather
          // than a sign-out, and blanking the user here would bounce a valid
          // session to the login page over a momentary error.
          return
        }
        await delay(AUTH_RETRY_BASE_MS * 2 ** attempt)
      }
    }
  }, [])

  useEffect(() => {
    refresh().finally(() => setLoading(false))
  }, [refresh])

  /** Persist a freshly signed-in session so it can be switched back to. */
  const remember = useCallback((account: SavedAccount) => {
    saveAccount(account)
    setAccounts(listAccounts())
    setActiveAccountIdState(account.id)
  }, [])

  const login = useCallback(
    async (username: string, password: string) => {
      const { access_token } = await api.login(username, password)
      // The previous session's media is keyed only by path, so it has to go
      // before the new account renders anything.
      clearMediaCache()
      clearCachedPushState()
      setToken(access_token)
      const me = await api.me()
      saveLastUser(me)
      setUserState(me)
      remember({
        id: me.id,
        username: me.username,
        display_name: me.display_name,
        avatar_color: me.avatar_color,
        avatar_url: me.avatar_url,
        token: access_token,
      })
      setSessionEpoch((value) => value + 1)
    },
    [remember],
  )

  const register = useCallback(
    async (data: { username: string; email: string; display_name: string; password: string }) => {
      const { access_token } = await api.register(data)
      clearMediaCache()
      clearCachedPushState()
      setToken(access_token)
      const me = await api.me()
      saveLastUser(me)
      setUserState(me)
      remember({
        id: me.id,
        username: me.username,
        display_name: me.display_name,
        avatar_color: me.avatar_color,
        avatar_url: me.avatar_url,
        token: access_token,
      })
      setSessionEpoch((value) => value + 1)
    },
    [remember],
  )

  /**
   * Sign out of the current account only.
   *
   * The other saved accounts stay put, so "sign out" here means "leave this
   * profile", not "forget everyone on this device". A full wipe is
   * `forgetAccount` on each, or signing out of the last one.
   */
  const logout = useCallback(() => {
    clearMediaCache()
    clearCachedPushState()
    if (activeAccountId !== null) removeAccount(activeAccountId)
    syncAccounts()
    setToken(null)
    clearLastUser()
    setUserState(null)
    setSessionEpoch((value) => value + 1)
  }, [activeAccountId, syncAccounts])

  /**
   * Swap to another saved account.
   *
   * The stored token is checked with `/auth/me` *before* it is adopted. A saved
   * token can have expired or been revoked since it was stored, and adopting a
   * dead one first would drop the user out of the account they are already
   * using. Verifying against the candidate token leaves the current session
   * untouched until the switch is known to be good.
   *
   * The global `loading` flag is deliberately not set here: `RequireAuth`
   * replaces the entire app with a spinner while it is true, which would unmount
   * the switcher and destroy the error it is about to show.
   */
  const switchAccount = useCallback(
    async (id: number) => {
      if (id === activeAccountId) return
      const account = listAccounts().find((item) => item.id === id)
      if (!account) throw new Error('That account is no longer saved on this device.')

      let me: User
      try {
        me = await api.meWithToken(account.token)
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          // The saved token is no longer valid. Drop the account rather than
          // leaving a profile in the switcher that can never be opened.
          removeAccount(id)
          syncAccounts()
          throw new Error('That account needs to sign in again.')
        }
        // A transient failure leaves the account in place; the caller can retry.
        throw error
      }

      // The token is good, so adopt it. Everything below is local bookkeeping
      // that cannot fail, which is what keeps the current session safe.
      clearMediaCache()
      clearCachedPushState()
      setToken(account.token)
      setActiveId(id)
      saveLastUser(me)
      setUserState(me)
      syncAccounts()
      setSessionEpoch((value) => value + 1)
    },
    [activeAccountId, syncAccounts],
  )

  /**
   * Adopt the account named in `?pulse_account=` on load.
   *
   * A push notification for an account that is not the one in front links here
   * with that account's id. The URL alone would land on the right page rendered
   * for the wrong account, so the switch happens before the caller's navigation
   * is left in place. The parameter is consumed once and stripped, so a refresh
   * or a shared link does not keep re-triggering it.
   *
   * Runs after the first `refresh()` resolves, so it can rely on `activeAccountId`
   * being settled. A failed switch is swallowed: the alert still lands the user
   * on the page, just under the account they already had.
   */
  useEffect(() => {
    if (loading) return
    const params = new URLSearchParams(window.location.search)
    const requested = Number(params.get('pulse_account'))
    if (!Number.isFinite(requested) || requested <= 0) return

    params.delete('pulse_account')
    const query = params.toString()
    window.history.replaceState(
      {},
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
    )

    if (requested === activeAccountId) return
    if (!listAccounts().some((account) => account.id === requested)) return
    void switchAccount(requested).catch(() => undefined)
  }, [loading, activeAccountId, switchAccount])

  const forgetAccount = useCallback(
    (id: number) => {
      const wasActive = id === activeAccountId
      removeAccount(id)
      syncAccounts()
      if (wasActive) {
        clearMediaCache()
        clearCachedPushState()
        clearLastUser()
        setUserState(null)
        setSessionEpoch((value) => value + 1)
      }
    },
    [activeAccountId, syncAccounts],
  )

  /**
   * Adopt a profile the caller already fetched (a settings save, say).
   *
   * The cached boot copy is refreshed here too, so the next load shows the
   * edit rather than the profile as it was before it.
   */
  const setUser = useCallback((next: User) => {
    saveLastUser(next)
    setUserState(next)
  }, [])

  const value = useMemo<AuthValue>(
    () => ({
      user,
      loading,
      login,
      register,
      logout,
      refresh,
      setUser,
      accounts,
      activeAccountId,
      switchAccount,
      forgetAccount,
      sessionEpoch,
    }),
    [
      user,
      loading,
      login,
      register,
      logout,
      refresh,
      accounts,
      activeAccountId,
      switchAccount,
      forgetAccount,
      sessionEpoch,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
