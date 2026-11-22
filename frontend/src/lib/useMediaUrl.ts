import { useEffect, useState } from 'react'
import {
  MediaError,
  dropMedia,
  peekMedia,
  refMedia,
  unrefMedia,
  type Entry,
  type MediaAsset,
} from './mediaCache'

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
export function useMediaStatus(
  path: string | null | undefined,
  /**
   * Bumped to ask for the bytes again after they may have changed on the
   * server. The path alone cannot express that: it is the same URL, but a
   * different response is now expected.
   */
  attempt = 0,
): {
  asset: MediaAsset | null
  failed: boolean
  loading: boolean
} {
  // The asset is stored with the path and attempt it belongs to. Keeping it
  // bare would let the previous element's picture stay on screen for a frame
  // after `path` changes, and a stale one after a retry.
  const [loaded, setLoaded] = useState<{
    path: string
    attempt: number
    asset: MediaAsset
  } | null>(null)
  const [failed, setFailed] = useState(false)

  // A retained download is painted from the first render. Without this the
  // element shows its placeholder until the promise callback runs, so a feed
  // that was already downloaded still appears to reload on every navigation.
  const key = path ?? ''
  const own = loaded && loaded.path === key && loaded.attempt === attempt ? loaded.asset : null
  const cached = path && attempt === 0 ? peekMedia(path) : null
  const asset = own ?? cached

  useEffect(() => {
    if (!path) {
      setLoaded(null)
      setFailed(false)
      return
    }

    // A new attempt means the previous response is stale, including a cached
    // object URL holding the old bytes.
    if (attempt > 0) dropMedia(path)

    let cancelled = false
    let timer: number | undefined
    let attemptCount = 0
    // The reference held for the attempt in flight, handed back before the next
    // one starts so a retry loop does not pile up references on a failed path.
    let held: Entry | null = null

    setLoaded(null)
    setFailed(false)

    const start = () => {
      if (cancelled) return
      if (held) unrefMedia(path, held)
      const entry = refMedia(path, attempt > 0)
      held = entry
      entry.promise
        .then((result) => {
          if (!cancelled) setLoaded({ path: key, attempt, asset: result })
        })
        .catch((error: unknown) => {
          if (cancelled) return
          const status = error instanceof MediaError ? error.status : 0
          // A 404 is the server saying the bytes are not there: retrying cannot
          // change that, and on this deployment it is the normal answer for
          // uploads lost with the instance's disk. Only 429, 5xx and a transport
          // failure are worth another attempt.
          const retryable = status === 0 || status === 429 || status >= 500
          if (!retryable) {
            setLoaded(null)
            setFailed(true)
            return
          }
          const rateLimited = status === 429
          const schedule = rateLimited ? RATE_LIMIT_DELAYS_MS : RETRY_DELAYS_MS
          const fallback = schedule[attemptCount]
          attemptCount += 1
          if (fallback === undefined) {
            setLoaded(null)
            setFailed(true)
            return
          }
          // Honour the server's own wait when it is longer than the schedule.
          const wait = rateLimited ? Math.max((error as MediaError).retryAfterMs ?? 0, fallback) : fallback
          timer = window.setTimeout(start, wait)
        })
    }

    start()

    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
      if (held) unrefMedia(path, held)
    }
  }, [path, attempt, key])

  return { asset, failed, loading: Boolean(path) && !asset && !failed }
}
