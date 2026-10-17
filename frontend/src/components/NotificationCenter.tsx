import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Avatar } from './Avatar'
import { CloseIcon } from './Icons'
import { useRealtime } from '../context/RealtimeContext'
import type { RealtimeEvent } from '../context/RealtimeContext'
import { playNotificationSound } from '../lib/sound'
import { isPageHidden, requestDesktopPermission, showDesktopNotification } from '../lib/desktopNotify'
import { useAuth } from '../context/AuthContext'
import type { RealtimeNotification } from '../lib/types'

interface Popup {
  key: string
  actorName: string
  actorColor: string
  actorUrl: string | null
  text: string
  /** Where clicking the popup should take the reader. */
  to: string
}

const POPUP_MS = 6000

function notificationText(event: RealtimeEvent): string | null {
  const kind = event.notification?.kind
  const name = event.actor?.display_name || 'Someone'
  switch (kind) {
    case 'like':
      return `${name} liked your post`
    case 'comment':
      return `${name} replied to your post`
    case 'follow':
      return `${name} started following you`
    case 'repost':
      return `${name} reposted your post`
    default:
      return null
  }
}

/**
 * Watches realtime events and surfaces them as popups and a chime.
 *
 * Lives inside the authenticated shell, so it only runs while a session is
 * active. Two channels are used on purpose: an in-page popup for when the tab
 * is visible, and an OS-level desktop notification for when it is hidden, since
 * an in-page element cannot be seen from a backgrounded tab.
 */
export function NotificationCenter() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [popups, setPopups] = useState<Popup[]>([])
  const { subscribe } = useRealtime()

  // Ask for desktop permission on the first notification rather than on mount,
  // so the prompt is tied to something the user just saw happen.
  const permissionAsked = useRef(false)

  const dismiss = useCallback((key: string) => {
    setPopups((current) => current.filter((popup) => popup.key !== key))
  }, [])

  const push = useCallback((popup: Popup) => {
    setPopups((current) => {
      // Cap the stack so a burst cannot cover the page.
      const next = [...current, popup]
      return next.length > 4 ? next.slice(next.length - 4) : next
    })
    window.setTimeout(() => dismiss(popup.key), POPUP_MS)
  }, [dismiss])

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message') {
          const message = event.message
          // The sender's own other tabs echo their message back; those are not
          // worth a chime or a popup.
          if (!message || message.mine) return
          playNotificationSound()
          const name = message.sender.display_name
          const body = message.body || 'Sent an attachment'
          if (isPageHidden()) {
            showDesktopNotification({
              title: name,
              body,
              tag: `dm-${message.conversation_id}`,
              onClick: () => navigate(`/messages/${message.conversation_id}`),
            })
          } else {
            push({
              key: `message-${message.id}`,
              actorName: name,
              actorColor: message.sender.avatar_color,
              actorUrl: message.sender.avatar_url,
              text: body,
              to: `/messages/${message.conversation_id}`,
            })
          }
          return
        }

        if (event.type === 'notification' && event.notification) {
          const notification = event.notification
          const text = notificationText(event)
          if (text === null) return
          playNotificationSound()

          if (isPageHidden()) {
            showDesktopNotification({
              title: 'Pulse',
              body: text,
              tag: `notification-${notification.id}`,
              onClick: () => navigate(notificationLink(notification)),
            })
            return
          }

          // Only prompt once per page load, and only now that a real event
          // arrived — the notification itself is the reason to ask.
          if (!permissionAsked.current) {
            permissionAsked.current = true
            void requestDesktopPermission()
          }
          push({
            key: `notification-${notification.id}`,
            actorName: event.actor?.display_name ?? 'Pulse',
            actorColor: event.actor?.avatar_color ?? '#6366f1',
            actorUrl: event.actor?.avatar_url ?? null,
            text,
            to: notificationLink(notification),
          })
        }
      }),
    [subscribe, push, navigate],
  )

  // A new session starts with an empty stack and a fresh permission ask.
  useEffect(() => {
    permissionAsked.current = false
    setPopups([])
  }, [user?.id])

  if (popups.length === 0) return null

  return (
    <div className="popup-stack" aria-live="polite">
      {popups.map((popup) => (
        <div key={popup.key} className="popup">
          <button
            type="button"
            className="popup-main"
            onClick={() => {
              navigate(popup.to)
              dismiss(popup.key)
            }}
          >
            <Avatar name={popup.actorName} color={popup.actorColor} url={popup.actorUrl} size="sm" />
            <span className="popup-text">{popup.text}</span>
          </button>
          <button
            type="button"
            className="popup-close"
            aria-label="Dismiss notification"
            onClick={() => dismiss(popup.key)}
          >
            <CloseIcon size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

function notificationLink(notification: RealtimeNotification): string {
  if (notification.post_id) return `/p/${notification.post_id}`
  if (notification.kind === 'follow') return '/notifications'
  return '/notifications'
}
