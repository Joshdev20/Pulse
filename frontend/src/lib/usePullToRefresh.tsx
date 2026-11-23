import { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/** How far the finger must travel before letting go refreshes. */
const TRIGGER = 68
/** The furthest the indicator can be dragged, so a long pull stays sane. */
const MAX_PULL = 108
/** Resistance: the indicator moves at just over half the finger's travel. */
const DRAG_RATIO = 0.55
/** How long the spinner is shown at minimum, so a fast refresh still reads. */
const MIN_SPIN_MS = 450

type Phase = 'idle' | 'pull' | 'armed' | 'refreshing'

/**
 * Drag the page down from the top to refresh it, the way a phone app does.
 *
 * The listeners are touch events, so a mouse or trackpad can never trigger
 * this: dragging with a pointer is a text selection. The browser's own
 * pull-to-refresh is suppressed (`overscroll-behavior` in the stylesheet) so
 * this is the only one that runs, and a pull only starts when the page is
 * already at the top — otherwise the gesture is an ordinary scroll and must
 * stay one.
 *
 * The indicator is rendered into its own root appended to `<body>` rather than
 * returned to the caller: a page only wants to say *when* to refresh, and a
 * fixed overlay does not belong inside whatever scroll container it was
 * declared in.
 */
export function usePullToRefresh(onRefresh: () => Promise<unknown> | unknown, enabled = true) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [distance, setDistance] = useState(0)

  // Refs, not state: the touch handlers are attached once and must read the
  // live values without being re-registered on every pixel of movement.
  const startY = useRef<number | null>(null)
  const pulling = useRef(false)
  const distanceRef = useRef(0)
  const refreshing = useRef(false)
  const handler = useRef(onRefresh)
  handler.current = onRefresh
  const host = useRef<{ node: HTMLDivElement; root: Root } | null>(null)

  useEffect(() => {
    const node = document.createElement('div')
    document.body.appendChild(node)
    const root = createRoot(node)
    host.current = { node, root }
    return () => {
      host.current = null
      // Unmounting a second root from inside the parent's commit is a race
      // React warns about; detach the node now and let the root go next tick.
      node.remove()
      window.setTimeout(() => root.unmount(), 0)
    }
  }, [])

  useEffect(() => {
    host.current?.root.render(<Indicator phase={phase} distance={distance} />)
  }, [phase, distance])

  const reset = useCallback(() => {
    pulling.current = false
    startY.current = null
    distanceRef.current = 0
    setDistance(0)
    setPhase('idle')
  }, [])

  const run = useCallback(async () => {
    refreshing.current = true
    setPhase('refreshing')
    setDistance(60)
    const started = Date.now()
    try {
      await handler.current()
    } catch {
      // The page owns its error state; a failed refresh just snaps back.
    } finally {
      // Hold the spinner briefly even when the refresh returns instantly;
      // otherwise the whole gesture reads as a flicker.
      const remaining = MIN_SPIN_MS - (Date.now() - started)
      if (remaining > 0) await new Promise((resolve) => window.setTimeout(resolve, remaining))
      refreshing.current = false
      reset()
    }
  }, [reset])

  useEffect(() => {
    if (!enabled) {
      reset()
      return
    }
    if (typeof window === 'undefined' || !('ontouchstart' in window)) return

    const onStart = (event: TouchEvent) => {
      if (refreshing.current || event.touches.length !== 1) return
      if (window.scrollY > 0) return
      // A pull that starts inside a scrolled pane (a horizontal gallery, a
      // textarea) belongs to that pane.
      const target = event.target as HTMLElement | null
      if (target?.closest('[data-no-pull]')) return
      startY.current = event.touches[0].clientY
      pulling.current = false
    }

    const onMove = (event: TouchEvent) => {
      if (startY.current === null || refreshing.current) return
      const dy = event.touches[0].clientY - startY.current
      if (dy <= 0) {
        // Moving up: this is a scroll, so give the gesture back.
        if (pulling.current) {
          pulling.current = false
          distanceRef.current = 0
          setDistance(0)
          setPhase('idle')
        }
        return
      }
      if (window.scrollY > 0) {
        startY.current = null
        return
      }
      // Only claim the gesture once the drag is clearly downward; a small
      // horizontal wobble at the top must still scroll or swipe as usual.
      if (!pulling.current && dy < 8) return
      pulling.current = true
      // The listener is non-passive precisely so this can stop the browser
      // from scrolling the page and from running its own refresh.
      event.preventDefault()
      const next = Math.min(MAX_PULL, dy * DRAG_RATIO)
      distanceRef.current = next
      setDistance(next)
      setPhase(next >= TRIGGER ? 'armed' : 'pull')
    }

    const onEnd = () => {
      if (startY.current === null) return
      startY.current = null
      const pulled = distanceRef.current
      pulling.current = false
      if (pulled >= TRIGGER) {
        void run()
        return
      }
      distanceRef.current = 0
      setDistance(0)
      setPhase('idle')
    }

    window.addEventListener('touchstart', onStart, { passive: true })
    window.addEventListener('touchmove', onMove, { passive: false })
    window.addEventListener('touchend', onEnd)
    window.addEventListener('touchcancel', onEnd)
    return () => {
      window.removeEventListener('touchstart', onStart)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onEnd)
      window.removeEventListener('touchcancel', onEnd)
    }
  }, [enabled, reset, run])
}

/** The floating pill: a ring that fills as you pull, then spins. */
function Indicator({ phase, distance }: { phase: Phase; distance: number }) {
  const progress = Math.min(1, distance / TRIGGER)
  return (
    <div
      className={`ptr${phase === 'refreshing' ? ' refreshing' : ''}${phase === 'armed' ? ' armed' : ''}`}
      style={
        {
          '--ptr-distance': `${distance}px`,
          '--ptr-progress': progress,
        } as React.CSSProperties
      }
      aria-hidden={phase === 'idle'}
      aria-live="polite"
    >
      <span className="ptr-pill">
        <svg className="ptr-ring" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <circle className="ptr-ring-track" cx="12" cy="12" r="9" />
          <circle
            className="ptr-ring-fill"
            cx="12"
            cy="12"
            r="9"
            pathLength={1}
            style={{ strokeDashoffset: 1 - progress }}
          />
        </svg>
        <span className="ptr-label">
          {phase === 'refreshing' ? 'Refreshing' : phase === 'armed' ? 'Release to refresh' : 'Pull to refresh'}
        </span>
      </span>
    </div>
  )
}
