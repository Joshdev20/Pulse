import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'

/**
 * Async data loader that ignores stale responses and supports manual refresh.
 * `deps` controls when the request re-runs.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    loader()
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
  }, [...deps, nonce])

  return { data, setData, loading, error, refresh: () => setNonce((value) => value + 1) }
}

/** Suggestions for the sidebar-ish "who to follow" card. */
export function useSuggested(limit = 3) {
  const { user } = useAuth()
  return useAsync(() => api.suggested(limit), [user?.id, limit])
}
