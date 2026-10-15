import { useEffect, useState } from 'react'
import { refMedia, unrefMedia, type MediaAsset } from './mediaCache'

export type { MediaAsset } from './mediaCache'

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

    setFailed(false)
    const entry = refMedia(path)
    let cancelled = false
    entry.promise
      .then((result) => {
        if (!cancelled) setAsset(result)
      })
      .catch(() => {
        if (cancelled) return
        setAsset(null)
        setFailed(true)
      })

    return () => {
      cancelled = true
      unrefMedia(path, entry)
    }
  }, [path])

  return { asset, failed, loading: Boolean(path) && !asset && !failed }
}
