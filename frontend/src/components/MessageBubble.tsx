import { useCallback, useEffect, useRef, useState } from 'react'
import { Avatar } from './Avatar'
import { MediaGallery } from './MediaGallery'
import { CheckIcon, ClockIcon, HeartIcon, ReplyIcon } from './Icons'
import { RelativeTime } from './UI'
import { STICKER_EMOJI } from '../lib/stickers'
import type { Message } from '../lib/types'

/**
 * One sticker, drawn large and without a bubble background.
 *
 * The emoji is rendered by the reader's own font rather than fetched as an
 * image, so it needs no network round trip and stays sharp at any size. The
 * label is exposed to assistive technology because an emoji alone reads as a
 * character name, not as the intent.
 */
function StickerView({ stickerId }: { stickerId: string }) {
  const entry = STICKER_EMOJI[stickerId]
  return (
    <span className="bubble-sticker" role="img" aria-label={entry?.label ?? 'Sticker'}>
      {entry?.emoji ?? '\u2728'}
    </span>
  )
}

/** How far right a bubble must travel before releasing sends it to reply. */
const REPLY_THRESHOLD = 64
/** Beyond this the bubble stops following the finger. */
const MAX_DRAG = 96
/** Hold this long without moving to open the action menu. */
const LONG_PRESS_MS = 450
/** Movement past this cancels the long press: it is a scroll or a swipe. */
const MOVE_TOLERANCE_PX = 10

export interface BubbleProps {
  message: Message
  /** Start a reply to this message (swipe right, or the menu). */
  onReply: (message: Message) => void
  /** Toggle the like on this message (double tap, or the menu). */
  onToggleLike: (message: Message) => void
  /** Open the long-press menu, anchored to this bubble. */
  onOpenMenu: (message: Message, anchor: DOMRect) => void
  /** The partner is online, so a delivered message can be marked seen. */
  partnerOnline: boolean
  /** Grouped with the message above it, so the avatar is drawn once. */
  grouped: boolean
}

/**
 * One message: swipe to reply, double-tap to like, long-press for the menu.
 *
 * Gestures use pointer events rather than touch events so the same code covers
 * touch, mouse and pen. Every gesture also has a keyboard-reachable equivalent:
 * the bubble is focusable, and Enter or Space opens the same menu a long press
 * does.
 */
export function MessageBubble({
  message,
  onReply,
  onToggleLike,
  onOpenMenu,
  partnerOnline,
  grouped,
}: BubbleProps) {
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [pressing, setPressing] = useState(false)
  // Mirrored in a ref as well: the pointer handlers run outside React's render,
  // so they cannot read the state update from this same event.
  const offsetRef = useRef(0)
  const startX = useRef<number | null>(null)
  const startY = useRef<number | null>(null)
  const lastTap = useRef(0)
  const tapTimer = useRef<number | null>(null)
  const pressTimer = useRef<number | null>(null)
  const longPressed = useRef(false)
  const bubbleRef = useRef<HTMLDivElement>(null)

  function setDrag(value: number) {
    offsetRef.current = value
    setOffset(value)
  }

  const cancelPress = useCallback(() => {
    if (pressTimer.current !== null) {
      window.clearTimeout(pressTimer.current)
      pressTimer.current = null
    }
    setPressing(false)
  }, [])

  useEffect(() => cancelPress, [cancelPress])

  function openMenu() {
    const rect = bubbleRef.current?.getBoundingClientRect()
    if (rect) onOpenMenu(message, rect)
  }

  function onPointerDown(event: React.PointerEvent) {
    if (event.button !== 0) return
    // Capture the pointer so a swipe keeps reporting moves after the finger
    // slides off the bubble — without this, the drag freezes mid-gesture and
    // the reply is never triggered on a touch screen.
    event.currentTarget.setPointerCapture?.(event.pointerId)
    startX.current = event.clientX
    startY.current = event.clientY
    longPressed.current = false
    setPressing(true)
    if (pressTimer.current !== null) window.clearTimeout(pressTimer.current)
    pressTimer.current = window.setTimeout(() => {
      pressTimer.current = null
      longPressed.current = true
      setPressing(false)
      // A selection can already be in progress by the time the hold completes,
      // if the browser started one before the CSS could stop it. Dropping it
      // here means the sheet never opens over a half-highlighted message.
      window.getSelection()?.removeAllRanges()
      // A short haptic tick confirms the menu opened, on devices that have one.
      navigator.vibrate?.(12)
      openMenu()
    }, LONG_PRESS_MS)
  }

  function onPointerMove(event: React.PointerEvent) {
    if (startX.current === null || startY.current === null) return
    const dx = event.clientX - startX.current
    const dy = event.clientY - startY.current
    // Any real movement means this is a scroll or a swipe, not a hold.
    if (Math.abs(dx) > MOVE_TOLERANCE_PX || Math.abs(dy) > MOVE_TOLERANCE_PX) cancelPress()
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

  function endDrag(event?: React.PointerEvent) {
    cancelPress()
    if (event) {
      const target = event.currentTarget
      if (target.hasPointerCapture?.(event.pointerId)) {
        target.releasePointerCapture(event.pointerId)
      }
    }
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
    endDrag(event)
    // A drag or a long press that ended on the bubble must not also count as a
    // tap.
    if (wasDragging || longPressed.current) return
    handleTap(event)
  }

  function handleTap(_event: React.PointerEvent) {
    const now = Date.now()
    if (now - lastTap.current < 300) {
      // Second tap inside the window: a double tap. Cancel the pending single
      // tap so a like is not followed by a spurious menu.
      if (tapTimer.current !== null) {
        window.clearTimeout(tapTimer.current)
        tapTimer.current = null
      }
      lastTap.current = 0
      onToggleLike(message)
      return
    }
    lastTap.current = now
    tapTimer.current = window.setTimeout(() => {
      tapTimer.current = null
      lastTap.current = 0
    }, 300)
  }

  const progress = Math.min(offset / REPLY_THRESHOLD, 1)
  const classes = [
    'bubble',
    message.mine ? 'mine' : 'theirs',
    dragging ? 'dragging' : '',
    pressing ? 'pressing' : '',
    message.sticker ? 'has-sticker' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={`bubble-row${message.mine ? ' mine' : ''}${grouped ? ' grouped' : ''}`}>
      {!message.mine &&
        (grouped ? (
          <span className="bubble-avatar-spacer" aria-hidden="true" />
        ) : (
          <Avatar
            name={message.sender.display_name}
            color={message.sender.avatar_color}
            url={message.sender.avatar_url}
            size="sm"
          />
        ))}
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
          ref={bubbleRef}
          className={classes}
          style={offset > 0 ? { transform: `translateX(${offset}px)` } : undefined}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={endDrag}
          onContextMenu={(event) => {
            event.preventDefault()
            openMenu()
          }}
          role="group"
          aria-label={`Message from ${message.sender.display_name}`}
          tabIndex={0}
          onKeyDown={(event) => {
            // Keyboard equivalent of the long press.
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              openMenu()
            }
          }}
        >
          {message.forwarded && (
            <span className="bubble-flag">
              <ReplyIcon size={12} />
              Forwarded
            </span>
          )}
          {message.reply_to && (
            <div className="bubble-quote">
              <span className="bubble-quote-name">{message.reply_to.sender_display_name}</span>
              <span className="bubble-quote-body">
                {message.reply_to.body || (message.reply_to.has_media ? 'Attachment' : '')}
              </span>
            </div>
          )}
          {message.sticker && <StickerView stickerId={message.sticker} />}
          {message.body && <p>{message.body}</p>}
          <MediaGallery items={message.media} />
          <div className="bubble-foot">
            <RelativeTime iso={message.created_at} className="bubble-time" />
            {message.edited_at && <span className="bubble-edited">edited</span>}
            {message.like_count > 0 && (
              <span className={`bubble-like-count${message.liked_by_me ? ' mine' : ''}`}>
                <HeartIcon size={12} filled />
                {message.like_count}
              </span>
            )}
            {message.mine && (
              <DeliveryTick
                read={message.read_by_partner}
                online={partnerOnline}
                pending={message.pending}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * The delivery mark on your own message: one tick when the message has reached
 * the server, two once the partner has read it.
 *
 * Read state comes from the server, not from a guess: `read_by_partner` is
 * derived from the partner's own read stamp, so a message never shows "seen"
 * because this side happened to be looking at the thread.
 */
function DeliveryTick({
  read,
  online,
  pending,
}: {
  read: boolean
  online: boolean
  pending?: boolean
}) {
  // Still on its way to the server: a clock says so, and it must not claim
  // "Delivered" just because the partner happens to be online.
  if (pending) {
    return (
      <span className="bubble-tick pending" aria-label="Sending" title="Sending">
        <ClockIcon size={12} />
      </span>
    )
  }
  if (read) {
    return (
      <span className="bubble-tick read" aria-label="Seen" title="Seen">
        <CheckIcon size={13} />
        <CheckIcon size={13} className="bubble-tick-second" />
      </span>
    )
  }
  return (
    <span
      className={`bubble-tick${online ? '' : ' pending'}`}
      aria-label={online ? 'Delivered' : 'Sent'}
      title={online ? 'Delivered' : 'Sent'}
    >
      {online ? <CheckIcon size={13} /> : <ClockIcon size={12} />}
    </span>
  )
}
