import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Avatar } from '../components/Avatar'
import { EmptyState, ErrorState, FeedSkeleton, RelativeTime } from '../components/UI'
import { BellIcon, CommentIcon, HeartIcon, UserIcon } from '../components/Icons'
import { useRealtime } from '../context/RealtimeContext'
import { api } from '../lib/api'
import type { Notification } from '../lib/types'

const ICONS = {
  like: HeartIcon,
  comment: CommentIcon,
  follow: UserIcon,
} as const

const VERB = {
  like: 'liked your post',
  comment: 'replied to your post',
  follow: 'started following you',
} as const

export function NotificationsPage() {
  const [items, setItems] = useState<Notification[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const { subscribe } = useRealtime()

  useEffect(() => {
    let active = true
    api
      .notifications()
      .then((data) => {
        if (active) setItems(data)
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Could not load notifications')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  // A live event carries only ids, so refetch to get the actor's profile.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type !== 'notification') return
        api
          .notifications()
          .then(setItems)
          .catch(() => undefined)
      }),
    [subscribe],
  )

  const unread = items.filter((item) => !item.read).length

  async function open(item: Notification) {
    if (!item.read) {
      setItems((current) => current.map((entry) => (entry.id === item.id ? { ...entry, read: true } : entry)))
      void api.markRead(item.id).catch(() => undefined)
    }
    if (item.post_id) navigate(`/p/${item.post_id}`)
    else if (item.actor) navigate(`/u/${item.actor.username}`)
  }

  async function markAll() {
    setItems((current) => current.map((entry) => ({ ...entry, read: true })))
    try {
      await api.markAllRead()
    } catch {
      // A failed mark-all is not worth interrupting the user; the next load resyncs.
    }
  }

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <div style={{ flex: 1 }}>
            <h1>Notifications</h1>
            <div className="sub">{unread > 0 ? `${unread} unread` : 'You are all caught up'}</div>
          </div>
          {unread > 0 && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={markAll}>
              Mark all read
            </button>
          )}
        </div>
      </header>

      {loading && <FeedSkeleton rows={5} />}
      {!loading && error && <ErrorState message={error} />}
      {!loading && !error && items.length === 0 && (
        <EmptyState
          icon={<BellIcon size={24} />}
          title="No notifications yet"
          description="Likes, replies and new followers will show up here."
        />
      )}

      {items.map((item) => {
        const Icon = ICONS[item.kind] ?? BellIcon
        return (
          <div
            key={item.id}
            className={`notification${item.read ? '' : ' unread'}`}
            onClick={() => void open(item)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void open(item)
            }}
            role="button"
            tabIndex={0}
          >
            <div className={`notification-icon ${item.kind}`}>
              <Icon size={17} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="notification-text">
                {item.actor ? (
                  <>
                    <strong>{item.actor.display_name}</strong> {VERB[item.kind] ?? 'sent you a notification'}
                  </>
                ) : (
                  'A notification'
                )}
                <RelativeTime iso={item.created_at} className="post-time" prefix=" · " />
              </div>
              {item.actor && <div className="notification-snippet">@{item.actor.username}</div>}
            </div>
            {item.actor && (
              <Link
                to={`/u/${item.actor.username}`}
                onClick={(event) => event.stopPropagation()}
                aria-label={`View ${item.actor.username}`}
              >
                <Avatar name={item.actor.display_name} color={item.actor.avatar_color} size="sm" />
              </Link>
            )}
          </div>
        )
      })}
    </>
  )
}
