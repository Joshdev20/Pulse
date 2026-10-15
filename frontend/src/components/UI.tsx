import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { CloseIcon } from './Icons'
import { timeAgo } from '../lib/format'
import { useNow } from '../lib/useNow'

export function Spinner({ size = 18 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden="true" />
}

/**
 * A timestamp that keeps itself current.
 *
 * The exact time is always in `title`, so hovering gives the full value while
 * the visible text stays short and re-renders as the shared clock ticks.
 */
export function RelativeTime({
  iso,
  className,
  prefix = '',
  suffix = '',
}: {
  iso: string
  className?: string
  prefix?: string
  suffix?: string
}) {
  const now = useNow()
  return (
    <time className={className} dateTime={iso} title={new Date(iso).toLocaleString()}>
      {prefix}
      {timeAgo(iso, now)}
      {suffix}
    </time>
  )
}

export function PostSkeleton() {
  return (
    <div className="skeleton-row">
      <div className="skeleton" style={{ width: 42, height: 42, borderRadius: '50%', flex: 'none' }} />
      <div className="skeleton-lines">
        <div className="skeleton" style={{ width: '38%', height: 13 }} />
        <div className="skeleton" style={{ width: '94%', height: 13 }} />
        <div className="skeleton" style={{ width: '62%', height: 13 }} />
      </div>
    </div>
  )
}

export function FeedSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <PostSkeleton key={index} />
      ))}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="state">
      <div className="state-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state">
      <div className="state-icon" style={{ color: 'var(--rose)' }}>!</div>
      <h3>Something went wrong</h3>
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

/** A dropdown that closes on outside click or Escape. */
export function Dropdown({
  trigger,
  children,
  align = 'right',
}: {
  trigger: (open: boolean) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="post-menu" ref={ref}>
      <button
        type="button"
        className="icon-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation()
          setOpen((value) => !value)
        }}
      >
        {trigger(open)}
      </button>
      {open && (
        <div className="menu" role="menu" style={align === 'left' ? { right: 'auto', left: 0 } : undefined}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="modal">
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}
