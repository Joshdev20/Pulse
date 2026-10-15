import { useEffect, useRef } from 'react'

/**
 * Call ``onLoadMore`` when the returned ref scrolls into view.
 *
 * Uses a sentinel element rather than a scroll listener so it works with any
 * scroll container, including the window on mobile. ``rootMargin`` starts the
 * fetch before the sentinel is actually visible, which hides the network
 * latency behind the user's own scrolling.
 */
export function useInfiniteScroll<T extends HTMLElement>(
  onLoadMore: () => void,
  options: { enabled?: boolean; rootMargin?: string } = {},
) {
  const { enabled = true, rootMargin = '600px' } = options
  const ref = useRef<T | null>(null)
  const callbackRef = useRef(onLoadMore)
  callbackRef.current = onLoadMore

  useEffect(() => {
    const node = ref.current
    if (!node || !enabled) return
    if (typeof IntersectionObserver === 'undefined') return

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) callbackRef.current()
      },
      { rootMargin, threshold: 0 },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [enabled, rootMargin])

  return ref
}
