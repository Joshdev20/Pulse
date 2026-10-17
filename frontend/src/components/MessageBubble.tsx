import { useRef, useState } from 'react'
import { Avatar } from './Avatar'
import { MediaGallery } from './MediaGallery'
import { HeartIcon, ReplyIcon } from './Icons'
import { RelativeTime } from './UI'
import type { Message } from '../lib/types'

/** How far right a bubble must travel before releasing sends it to reply. */
const REPLY_THRESHOLD = 64
/** Beyond this the bubble stops following the finger. */
const MAX_DRAG = 96

interface BubbleProps {
  message: Message
  /** Start a reply to this message (swipe right, or the reply button). */
  onReply: (message: Message) => void
  /** Toggle the like on this message (double tap, or the heart button). */
  onToggleLike: (message: Message) => void
}

/**
 * One message, with swipe-to-reply and double-tap-to-like.
 *
 * Gestures are implemented with pointer events rather than touch events so the
 * same code covers touch, mouse and pen. The tap targets are unchanged: a
 * pointer that never moves and never doubles is a plain click, so the reply and
 * like buttons keep working for anyone who cannot perform the gestures.
 */
export function MessageBubble({ message, onReply, onToggleLike }: BubbleProps) {
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  // Mirrored in a ref as well: the pointer handlers run outside React's render,
  // so they cannot read the state update from this same event.
  const offsetRef = useRef(0)
  const startX = useRef<number | null>(null)
  const startY = useRef<number | null>(null)
  const lastTap = useRef(0)
  const tapTimer = useRef<number | null>(null)

  function setDrag(value: number) {
    offsetRef.current = value
    setOffset(value)
  }

  function onPointerDown(event: React.PointerEvent) {
    // Only the primary button drags; a right-click should open the menu.
    if (event.button !== 0) return
    startX.current = event.clientX
    startY.current = event.clientY
  }

  function onPointerMove(event: React.PointerEvent) {
    if (startX.current === null || startY.current === null) return
    const dx = event.clientX - startX.current
    const dy = event.clientY - startY.current
    // A vertical drag is a scroll, not a reply; abandon the gesture so the
    // thread can still be scrolled with the thumb on a bubble.
    if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 12) {
      startX.current = null
      setDrag(0)
      setDragging(false)
      return
    }
    if (dx <= 0) {
      setDrag(0)
      return
    }
    if (!dragging) setDragging(true)
    setDrag(Math.min(dx, MAX_DRAG))
  }

  function endDrag() {
    if (startX.current === null) return
    startX.current = null
    startY.current = null
    const travelled = offsetRef.current
    setDragging(false)
    if (travelled >= REPLY_THRESHOLD) onReply(message)
    setDrag(0)
  }

  function onPointerUp(event: React.PointerEvent) {
    const wasDragging = dragging
    endDrag()
    // A drag that ended on the bubble must not also count as a tap.
    if (wasDragging) return
    void handleTap(event)
  }

  function handleTap(_event: React.PointerEvent) {
    const now = Date.now()
    if (now - lastTap.current < 300) {
      // Second tap inside the window: a double tap. Cancel the pending single
      // tap so a like is not followed by a spurious reply.
      if (tapTimer.current !== null) {
        window.clearTimeout(tapTimer.current)
        tapTimer.current = null
      }
      lastTap.current = 0
      onToggleLike(message)
      return
    }
    lastTap.current = now
    // Held briefly in case a second tap follows. A single tap does nothing on
    // its own, so this only exists to keep the double-tap window open.
    tapTimer.current = window.setTimeout(() => {
      tapTimer.current = null
      lastTap.current = 0
    }, 300)
  }

  const progress = Math.min(offset / REPLY_THRESHOLD, 1)

  return (
    <div className={`bubble-row${message.mine ? ' mine' : ''}`}>
      {!message.mine && (
        <Avatar
          name={message.sender.display_name}
          color={message.sender.avatar_color}
          url={message.sender.avatar_url}
          size="sm"
        />
      )}
      <div className="bubble-wrap">
        {offset > 0 && (
          <span
            className="bubble-reply-hint"
            aria-hidden="true"
            style={{ opacity: progress, transform: `scale(${0.7 + progress * 0.3})` }}
          >
            <ReplyIcon size={17} />
          </span>
        )}
        <div
          className={`bubble${message.mine ? ' mine' : ''}${dragging ? ' dragging' : ''}`}
          style={offset > 0 ? { transform: `translateX(${offset}px)` } : undefined}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={endDrag}
          role="group"
          aria-label={`Message from ${message.sender.display_name}`}
        >
          {message.reply_to && (
            <div className="bubble-quote">
              <span className="bubble-quote-name">{message.reply_to.sender_display_name}</span>
              <span className="bubble-quote-body">
                {message.reply_to.body || (message.reply_to.has_media ? 'Attachment' : '')}
              </span>
            </div>
          )}
          {message.body && <p>{message.body}</p>}
          <MediaGallery items={message.media} />
          <div className="bubble-foot">
            <RelativeTime iso={message.created_at} className="bubble-time" />
            {message.like_count > 0 && (
              <span className={`bubble-like-count${message.liked_by_me ? ' mine' : ''}`}>
                <HeartIcon size={12} filled />
                {message.like_count}
              </span>
            )}
          </div>
        </div>

        {/* Visible affordances, so the gestures are discoverable and usable
            without them. Hidden until the bubble is hovered or focused. */}
        <div className="bubble-actions">
          <button
            type="button"
            className="bubble-action"
            onClick={() => onReply(message)}
            aria-label="Reply to this message"
            title="Reply"
          >
            <ReplyIcon size={15} />
          </button>
          <button
            type="button"
            className={`bubble-action${message.liked_by_me ? ' liked' : ''}`}
            onClick={() => onToggleLike(message)}
            aria-label={message.liked_by_me ? 'Remove like' : 'Like this message'}
            aria-pressed={message.liked_by_me}
            title={message.liked_by_me ? 'Unlike' : 'Like'}
          >
            <HeartIcon size={15} filled={message.liked_by_me} />
          </button>
        </div>
      </div>
    </div>
  )
}
