import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  CloseIcon,
  CopyIcon,
  EditIcon,
  ForwardIcon,
  HeartIcon,
  ReplyIcon,
  TrashIcon,
} from './Icons'
import type { Message } from '../lib/types'

export type MessageAction = 'reply' | 'like' | 'copy' | 'edit' | 'forward' | 'delete'

interface Props {
  message: Message
  /** The bubble the sheet is anchored to, so it opens next to the message. */
  anchor: DOMRect | null
  onAction: (action: MessageAction) => void
  onClose: () => void
}

interface ActionItem {
  action: MessageAction
  label: string
  icon: React.ReactNode
  /** Rendered in the rose tone, for the one action that cannot be undone. */
  danger?: boolean
}

/**
 * The long-press menu for one message.
 *
 * A sheet on a phone and a small card beside the bubble on a desktop, because
 * the same menu has to work with a thumb and with a mouse. Every action is a
 * real button so the menu is reachable by keyboard, and the overlay closes on
 * Escape, on a backdrop press, and on scroll — a menu left floating over a
 * thread that has moved underneath it would point at the wrong message.
 */
export function MessageActionSheet({ message, anchor, onAction, onClose }: Props) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [placement, setPlacement] = useState<{ top: number; left: number } | null>(null)

  const actions: ActionItem[] = [
    { action: 'reply', label: 'Reply', icon: <ReplyIcon size={18} /> },
    {
      action: 'like',
      label: message.liked_by_me ? 'Remove like' : 'Like',
      icon: <HeartIcon size={18} filled={message.liked_by_me} />,
    },
    ...(message.body
      ? [{ action: 'copy' as const, label: 'Copy text', icon: <CopyIcon size={18} /> }]
      : []),
    ...(message.mine && message.body
      ? [{ action: 'edit' as const, label: 'Edit', icon: <EditIcon size={18} /> }]
      : []),
    { action: 'forward', label: 'Forward', icon: <ForwardIcon size={18} /> },
    ...(message.mine
      ? [
          {
            action: 'delete' as const,
            label: 'Delete',
            icon: <TrashIcon size={18} />,
            danger: true,
          },
        ]
      : []),
  ]

  // Placed after mount so the card's own height is known: a menu opened near
  // the bottom of the thread has to flip above the bubble instead of running
  // off the viewport.
  useLayoutEffect(() => {
    if (!anchor) return
    const card = cardRef.current
    const height = card?.offsetHeight ?? 260
    const width = card?.offsetWidth ?? 220
    const gap = 8
    const below = anchor.bottom + gap
    const fitsBelow = below + height <= window.innerHeight - 12
    const top = fitsBelow ? below : Math.max(12, anchor.top - height - gap)
    const left = Math.min(
      Math.max(12, anchor.left),
      Math.max(12, window.innerWidth - width - 12),
    )
    setPlacement({ top, left })
  }, [anchor])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    // Capture phase: the thread scrolls its own box, so a bubbling listener on
    // document would miss it.
    const onScroll = () => onClose()
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [onClose])

  // The first entry takes focus so a keyboard user lands inside the menu rather
  // than on the backdrop.
  const firstRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    firstRef.current?.focus({ preventScroll: true })
  }, [])

  return (
    <div
      className="msg-sheet-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
      onContextMenu={(event) => event.preventDefault()}
      role="presentation"
    >
      <div
        ref={cardRef}
        className="msg-sheet"
        style={placement ? { top: placement.top, left: placement.left } : { visibility: 'hidden' }}
        role="menu"
        aria-label="Message actions"
      >
        <div className="msg-sheet-head">
          <span className="msg-sheet-preview">
            {message.body || (message.sticker ? 'Sticker' : 'Attachment')}
          </span>
          <button type="button" className="icon-btn" aria-label="Close menu" onClick={onClose}>
            <CloseIcon size={15} />
          </button>
        </div>
        <div className="msg-sheet-actions">
          {actions.map((item, index) => (
            <button
              key={item.action}
              ref={index === 0 ? firstRef : undefined}
              type="button"
              role="menuitem"
              className={`msg-sheet-item${item.danger ? ' danger' : ''}`}
              onClick={() => onAction(item.action)}
            >
              <span className="msg-sheet-icon">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
