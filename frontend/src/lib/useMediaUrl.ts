import { useEffect, useState } from 'react'
import { MediaError, refMedia, unrefMedia, type Entry, type MediaAsset } from './mediaCache'

export type { MediaAsset } from './mediaCache'

// Backoff used when the server did not say how long to wait. A download that
// fails is usually transient: a burst that tripped the rate limiter, a token
// mid-refresh, or a backend that was briefly down. Without a retry the element
// keeps its alt text for the rest of the session, because this effect never runs
// again for the same path.
const RETRY_DELAYS_MS = [500, 1500, 4000]

// A 429 carries the window length, which is minutes on this API, so it is
// retried on its own larger schedule rather than the short one above.
const RATE_LIMIT_DELAYS_MS = [3000, 15000, 45000, 60000]

/**
 * Load protected media into a blob URL.
 *
 * The download is shared and reference counted through `mediaCache`, so several
 * elements showing the same attachment share one request. Pass a path of `null`
 * to skip loading entirely — that is how callers defer work until an element is
 * about to be seen.
 */
export function useMediaUrl(path: string | null | undefined): MediaAsset | null {
  return useMediaStatus(path).asset
}

/**
 * `useMediaUrl` plus the state needed to render a placeholder honestly.
 *
 * A caller that shows a loading shimmer has to know the difference between
 * "still downloading" and "will never arrive", otherwise a failed avatar sits
 * behind its skeleton forever.
 */
export function useMediaStatus(path: string | null | undefined): {
  asset: MediaAsset | null
  failed: boolean
  loading: boolean
} {
  const [asset, setAsset] = useState<MediaAsset | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!path) {
      setAsset(null)
      setFailed(false)
      return
    }

    let cancelled = false
    let timer: number | undefined
    let attempt = 0
    // The reference held for the attempt in flight, handed back before the next
    // one starts so a retry loop does not pile up references on a failed path.
    let held: Entry | null = null

    setAsset(null)
    setFailed(false)

    const start = () => {
      if (cancelled) return
      if (held) unrefMedia(path, held)
      const entry = refMedia(path)
      held = entry
      entry.promise
        .then((result) => {
          if (!cancelled) setAsset(result)
        })
        .catch((error: unknown) => {
          if (cancelled) return
          const rateLimited = error instanceof MediaError && error.status === 429
          const schedule = rateLimited ? RATE_LIMIT_DELAYS_MS : RETRY_DELAYS_MS
          const fallback = schedule[attempt]
          attempt += 1
          if (fallback === undefined) {
            setAsset(null)
            setFailed(true)
            return
          }
          // Honour the server's own wait when it is longer than the schedule.
          const wait = rateLimited ? Math.max(error.retryAfterMs ?? 0, fallback) : fallback
          timer = window.setTimeout(start, wait)
        })
    }

    start()

    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
      if (held) unrefMedia(path, held)
    }
  }, [path])

  return { asset, failed, loading: Boolean(path) && !asset && !failed }
}
