import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import * as queryCache from '../lib/queryCache'

/**
 * Async data loader that ignores stale responses and supports manual refresh.
 * `deps` controls when the request re-runs.
 *
 * Pass a `cacheKey` to paint the previous result immediately when the component
 * mounts again — navigating away and back should show what was already there,
 * not a skeleton. A cached value is served for `queryCache.DEFAULT_TTL_MS`; past
 * that it is still shown while a fresh copy is fetched behind it, so the screen
 * never goes blank on a revalidation.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = [], cacheKey?: string) {
  const [data, setData] = useState<T | null>(() =>
    cacheKey ? (queryCache.read<T>(cacheKey) ?? null) : null,
  )
  // Callers use `setData` to apply a server response locally (a follow toggling
  // its own counts, an edit returning the updated record), and some pass an
  // updater rather than a value. Writing the result through to the cache keeps
  // the two in step; without it the component would show the new value while the
  // cache kept the old one, and the next mount would bring the old value back.
  const applyData = useCallback(
    (next: T | null | ((current: T | null) => T | null)) => {
      setData((current) => {
        const value = typeof next === 'function' ? (next as (c: T | null) => T | null)(current) : next
        if (cacheKey && value !== null) queryCache.write(cacheKey, value)
        return value
      })
    },
    [cacheKey],
  )
  // Only a cold cache shows a skeleton. A warm one paints at once, and the
  // revalidation that follows must not replace the content with a spinner.
  const [loading, setLoading] = useState(() => !(cacheKey && queryCache.read(cacheKey) !== undefined))
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader
  // A manual refresh means "go and ask", so it skips the copy the cache would
  // otherwise serve for its full TTL — which is the whole point of pulling.
  const forceNext = useRef(false)

  useEffect(() => {
    let active = true
    const cached = cacheKey ? queryCache.read(cacheKey) : undefined
    if (cached !== undefined) setData(cached as T)
    setLoading(cached === undefined)
    setError(null)

    const request = cacheKey
      ? queryCache.load(cacheKey, () => loaderRef.current(), undefined, forceNext.current)
      : loaderRef.current()
    forceNext.current = false

    request
      .then((result) => {
        if (active) setData(result)
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Request failed')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, cacheKey])

  return {
    data,
    setData: applyData,
    loading,
    error,
    refresh: () => {
      forceNext.current = true
      setNonce((value) => value + 1)
    },
  }
}

/** Suggestions for the sidebar-ish "who to follow" card. */
export function useSuggested(limit = 3) {
  const { user } = useAuth()
  return useAsync(() => api.suggested(limit), [user?.id, limit], `suggested:${user?.id}:${limit}`)
}
