import { useEffect, useRef, useState } from 'react'

/**
 * Whether an element has come near the viewport.
 *
 * Used to defer work — fetching a protected image, say — until it is about to
 * be seen. Once triggered it stays true, so scrolling back up does not throw
 * the loaded asset away.
 *
 * `rootMargin` starts the work slightly before the element is on screen, which
 * is what makes the result feel immediate rather than arriving late.
 */
export function useInView<T extends HTMLElement>(
  { rootMargin = '300px' }: { rootMargin?: string } = {},
) {
  const ref = useRef<T | null>(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    if (inView) return
    const element = ref.current
    if (!element) return

    // No IntersectionObserver (or an old browser): load immediately rather
    // than never.
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true)
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true)
          observer.disconnect()
        }
      },
      { rootMargin },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [inView, rootMargin])

  return { ref, inView }
}
