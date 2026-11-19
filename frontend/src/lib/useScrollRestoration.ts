import { useEffect, useRef } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

/**
 * Remember where each page was scrolled to, and put it back on the way back.
 *
 * Going into a post and returning used to land the reader at the top of a feed
 * they had already scrolled through. The position is only restored on POP —
 * the browser's back and forward — because following a link is a request for a
 * new page and should start at the top.
 *
 * Positions are held in memory for the tab's lifetime. Restoring is retried
 * across a few frames: the feed renders from cache synchronously, but media
 * still settles the page height over the next tick, and a scroll applied before
 * the document is tall enough is clamped to whatever height exists.
 */
const positions = new Map<string, number>()

/** Frames to keep retrying a restore while the page grows under it. */
const RESTORE_ATTEMPTS = 20

export function useScrollRestoration(): void {
  const { pathname, search } = useLocation()
  const navigationType = useNavigationType()
  const key = `${pathname}${search}`
  const previousKey = useRef(key)

  useEffect(() => {
    const leaving = previousKey.current
    previousKey.current = key

    // Record where the page being left was scrolled to. Capture runs on every
    // change, so the position is saved before the new route paints over it.
    const save = () => positions.set(leaving, window.scrollY)
    save()

    if (navigationType !== 'POP') {
      window.scrollTo(0, 0)
      return
    }

    const target = positions.get(key)
    if (target === undefined) {
      window.scrollTo(0, 0)
      return
    }

    let frame = 0
    let cancelled = false
    const attempt = () => {
      if (cancelled) return
      window.scrollTo(0, target)
      // Stop as soon as the position held; otherwise the document is still
      // shorter than the target and another frame is worth trying.
      if (Math.abs(window.scrollY - target) <= 1) return
      if (++frame < RESTORE_ATTEMPTS) requestAnimationFrame(attempt)
    }
    requestAnimationFrame(attempt)

    return () => {
      cancelled = true
    }
  }, [key, navigationType])
}
