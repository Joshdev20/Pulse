import { useCallback, useState } from 'react'

/**
 * Whether an `<img>`/`<video>` has finished decoding, without missing the event.
 *
 * `onLoad` alone is not reliable here: media arrives as a blob URL whose bytes
 * are already in memory, so the element can finish decoding before React
 * attaches the handler and the event is never delivered — leaving a cached
 * picture stuck behind its placeholder. Attaching the ref re-checks the
 * element's own `complete`/`readyState` flag, which closes that race.
 */
export function useMediaReady(src: string | null | undefined) {
  const [ready, setReady] = useState(false)
  const [element, setElement] = useState<HTMLImageElement | HTMLVideoElement | null>(null)
  const [seenSrc, setSeenSrc] = useState(src)

  // Re-check on attach and whenever the source changes: a fresh source means a
  // fresh decode, and the old `ready` must not carry over.
  if (src !== seenSrc) {
    setSeenSrc(src)
    setReady(false)
  }

  if (element && !ready) {
    const decoded =
      element instanceof HTMLVideoElement ? element.readyState >= 1 : element.complete
    if (decoded) setReady(true)
  }

  const ref = useCallback((node: HTMLImageElement | HTMLVideoElement | null) => {
    setElement(node)
  }, [])

  return { ref, ready, markReady: () => setReady(true) }
}
