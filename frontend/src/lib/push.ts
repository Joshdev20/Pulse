/**
 * Web Push registration, so alerts reach the user with Pulse closed.
 *
 * The in-page popup and the OS `Notification` API both need a live page: the
 * first a visible tab, the second merely an open one. Neither exists once the
 * browser is shut, which is what this module fixes. It registers the service
 * worker, subscribes against the browser's push service using the server's
 * VAPID public key, and hands the resulting endpoint to the API so the server
 * can push later.
 *
 * Two environment details matter and are handled here:
 *
 *  - The public key must be base64url-decoded to a `Uint8Array` before it can be
 *    passed as `applicationServerKey`; browsers reject the raw string.
 *  - The key is fetched from the API rather than baked into the bundle, because
 *    the bundle is built once and the key belongs to the deployment. Rotating
 *    the key pair must not require a rebuild.
 */

import { api } from './api'

export type PushState =
  | 'unsupported'
  | 'unconfigured'
  | 'default'
  | 'granted'
  | 'denied'
  | 'subscribed'

/** Whether this browser can do Web Push at all. */
export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return Notification.permission
}

/** base64url -> Uint8Array, the shape `applicationServerKey` requires. */
function decodeKey(base64: string): Uint8Array<ArrayBuffer> {
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
  const normalised = padded.replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(normalised)
  // Backed by a plain ArrayBuffer (not the shared kind) so it satisfies the
  // `BufferSource` that `applicationServerKey` expects.
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
  return bytes
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // `updateViaCache: 'none'` so a new service worker is picked up on the next
  // load instead of being served from the HTTP cache.
  return navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
}

/** The current state, without prompting for anything. */
export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return 'unsupported'
  const status = await api.pushStatus().catch(() => null)
  if (!status?.enabled || !status.public_key) return 'unconfigured'
  if (Notification.permission === 'denied') return 'denied'
  if (Notification.permission === 'default') return 'default'
  const reg = await navigator.serviceWorker.getRegistration('/')
  const subscription = await reg?.pushManager.getSubscription()
  return subscription ? 'subscribed' : 'granted'
}

/**
 * Register, ask permission, and subscribe. Returns the resulting state.
 *
 * Must be called from a user gesture: browsers refuse the permission prompt
 * otherwise. Every failure is reported as a state rather than thrown, so the
 * settings screen can show what happened instead of breaking.
 */
export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return 'unsupported'
  const status = await api.pushStatus().catch(() => null)
  if (!status?.enabled || !status.public_key) return 'unconfigured'

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return permission === 'denied' ? 'denied' : 'default'

  const reg = await registration()
  await navigator.serviceWorker.ready

  const existing = await reg.pushManager.getSubscription()
  const subscription =
    existing ??
    (await reg.pushManager.subscribe({
      // Required by Chrome: a push without a visible notification is an error.
      userVisibleOnly: true,
      applicationServerKey: decodeKey(status.public_key),
    }))

  const json = subscription.toJSON() as {
    endpoint?: string
    keys?: { p256dh?: string; auth?: string }
  }
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) return 'default'

  await api.pushSubscribe({
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
  })
  return 'subscribed'
}

/** Cancel the browser subscription and forget it on the server. */
export async function disablePush(): Promise<PushState> {
  if (!pushSupported()) return 'unsupported'
  const reg = await navigator.serviceWorker.getRegistration('/')
  const subscription = await reg?.pushManager.getSubscription()
  if (subscription) {
    // Tell the server first: once the browser subscription is gone we no longer
    // have the endpoint to send, and the row would linger forever.
    await api.pushUnsubscribe(subscription.endpoint).catch(() => undefined)
    await subscription.unsubscribe().catch(() => undefined)
  }
  return notificationPermission() === 'granted' ? 'granted' : 'default'
}

/**
 * Re-register on load if this browser is already subscribed.
 *
 * Browsers can rotate a subscription's endpoint on their own; when that
 * happens the stored row points at a dead endpoint and every push to it is
 * discarded. Re-sending the current subscription on each authenticated load
 * keeps the server's copy current, and is cheap: it is one request.
 */
export async function syncPushSubscription(): Promise<void> {
  if (!pushSupported() || Notification.permission !== 'granted') return
  try {
    const reg = await navigator.serviceWorker.getRegistration('/')
    const subscription = await reg?.pushManager.getSubscription()
    if (!subscription) return
    const json = subscription.toJSON() as {
      endpoint?: string
      keys?: { p256dh?: string; auth?: string }
    }
    if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) return
    await api.pushSubscribe({
      endpoint: json.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
    })
  } catch {
    // A failed re-sync is not worth surfacing; the next load tries again.
  }
}
