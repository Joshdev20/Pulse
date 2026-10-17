/**
 * Desktop notifications, so a popup still appears when the Pulse tab is in the
 * background or hidden.
 *
 * These are OS-level notifications, which is the only way to reach a user who
 * is not looking at the page: an in-page toast is invisible to a backgrounded
 * tab. Permission is requested lazily on the first event rather than on load,
 * because a permission prompt out of nowhere is the usual reason people deny it.
 */

export type DesktopPermission = 'default' | 'granted' | 'denied' | 'unsupported'

export function desktopPermission(): DesktopPermission {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return Notification.permission as DesktopPermission
}

/**
 * Ask for permission, but only when it can still be answered.
 *
 * Returns the resulting state. Never throws: a denied or unsupported browser
 * simply falls back to the in-page popup.
 */
export async function requestDesktopPermission(): Promise<DesktopPermission> {
  if (desktopPermission() !== 'default') return desktopPermission()
  try {
    return (await Notification.requestPermission()) as DesktopPermission
  } catch {
    return 'denied'
  }
}

/** Whether the tab is currently visible to the user. */
export function isPageHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

/**
 * Show a desktop notification for an event that happened while the page was
 * hidden. Returns whether one was actually shown, so the caller can decide to
 * fall back to an in-page popup.
 */
export function showDesktopNotification(options: {
  title: string
  body: string
  tag?: string
  onClick?: () => void
}): boolean {
  if (desktopPermission() !== 'granted') return false
  try {
    const notification = new Notification(options.title, {
      body: options.body,
      tag: options.tag,
      // Same-origin icon, so no external fetch and no CSP exception.
      icon: '/favicon.ico',
    })
    notification.onclick = () => {
      window.focus()
      options.onClick?.()
      notification.close()
    }
    return true
  } catch {
    return false
  }
}
