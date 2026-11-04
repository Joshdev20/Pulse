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
 * The branded icon for an actor, as a same-origin path the OS can fetch.
 *
 * Same-origin rather than the API's own host: the service worker resolves it
 * against the site origin, and the deployment rewrites the route to the API.
 * That keeps one path working in dev (where Vite proxies `/api`) and in
 * production (where the rewrite forwards it), without the frontend needing to
 * know the API's origin. Undefined for an unknown actor, so the caller falls
 * back to the app icon rather than requesting an id that does not exist.
 */
export function notificationIconUrl(userId: number | null | undefined): string | undefined {
  return userId == null ? undefined : `/api/push/icon/${userId}.png`
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
  /**
   * The sender's branded avatar icon. A same-origin path the app already serves
   * through the API's icon route; omitted, the app icon is used.
   */
  icon?: string
  onClick?: () => void
}): boolean {
  if (desktopPermission() !== 'granted') return false
  try {
    const notification = new Notification(options.title, {
      body: options.body,
      tag: options.tag,
      // The sender's avatar when one was supplied, so the popup names who it is
      // about; the app icon otherwise. Both are same-origin, so no external
      // fetch and no CSP exception.
      icon: options.icon ?? '/icon-192.png',
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
