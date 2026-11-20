import { useEffect, useRef } from 'react'
import { PhoneIcon, VideoIcon } from './Icons'
import { RelativeTime } from './UI'
import { callDetail, callLabel } from '../lib/calls-format'
import type { Message } from '../lib/types'

/** A hold this long opens the menu; the same threshold the bubbles use. */
const LONG_PRESS_MS = 450
/** Movement past this is a scroll, not a hold. */
const MOVE_TOLERANCE_PX = 10

/**
 * One call, as it appears in the thread.
 *
 * A centred pill rather than a bubble, because a call has no author to attribute
 * and no reply to make: it is an event that happened between the two people. The
 * wording comes from the shared formatter so the inbox row and this entry cannot
 * disagree about what the same call was.
 *
 * The state drives the tone: a missed or declined call is the one that has to
 * catch the eye, so it carries the warning colour and a solid icon.
 *
 * It takes the same hold-to-open menu as a bubble: a call entry can be deleted,
 * and holding is how every other message in the thread is managed.
 */
export function CallEntry({
  message,
  onOpenMenu,
}: {
  message: Message
  /** Called with the pill's box, so the menu opens beside it. */
  onOpenMenu?: (message: Message, anchor: DOMRect) => void
}) {
  const timer = useRef<number | null>(null)
  const origin = useRef<{ x: number; y: number } | null>(null)

  const call = message.call
  if (!call) return null

  const live = call.state === 'ringing' || call.state === 'active'
  const missed = call.state === 'missed' || call.state === 'declined' || call.state === 'cancelled'
  const detail = callDetail(call)
  const Icon = call.mode === 'video' ? VideoIcon : PhoneIcon

  function cancel() {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    origin.current = null
  }

  // A hold that begins on a row and then scrolls is a scroll. Only one pointer
  // move may arrive before the row slides out from under the finger, so the
  // scroll itself has to abandon the hold as well.
  useEffect(() => {
    if (!onOpenMenu) return
    window.addEventListener('scroll', cancel, { capture: true, passive: true })
    return () => window.removeEventListener('scroll', cancel, { capture: true })
  }, [onOpenMenu])

  function open(element: HTMLElement) {
    onOpenMenu?.(message, element.getBoundingClientRect())
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !onOpenMenu) return
    // Captured now: React nulls `currentTarget` once the handler returns, so
    // reading it inside the timer below would open the menu against null.
    const element = event.currentTarget
    origin.current = { x: event.clientX, y: event.clientY }
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      timer.current = null
      navigator.vibrate?.(12)
      open(element)
    }, LONG_PRESS_MS)
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const start = origin.current
    if (!start) return
    // Movement past the tolerance is a scroll, so the hold is abandoned rather
    // than opening a menu the reader did not ask for.
    if (
      Math.abs(event.clientX - start.x) >= MOVE_TOLERANCE_PX ||
      Math.abs(event.clientY - start.y) >= MOVE_TOLERANCE_PX
    ) {
      cancel()
    }
  }

  return (
    <div
      className={`call-entry${missed ? ' missed' : ''}${live ? ' live' : ''}`}
      role="note"
      aria-label={`${callLabel(call)}${detail ? `, ${detail}` : ''}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={cancel}
      onPointerCancel={cancel}
      onContextMenu={(event) => {
        if (!onOpenMenu) return
        event.preventDefault()
        cancel()
        open(event.currentTarget)
      }}
    >
      <span className="call-entry-icon" aria-hidden="true">
        <Icon size={15} />
      </span>
      <span className="call-entry-text">
        <span className="call-entry-label">{callLabel(call)}</span>
        {detail && <span className="call-entry-detail">{detail}</span>}
      </span>
      <RelativeTime iso={message.created_at} className="call-entry-time" />
    </div>
  )
}
