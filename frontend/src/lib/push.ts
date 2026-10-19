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
import type { PushSubscriptionBody } from './types'

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

/** The API body for a browser subscription, or null if it is unusable. */
function subscriptionBody(
  subscription: PushSubscription,
): { endpoint: string; keys: { p256dh: string; auth: string } } | null {
  const json = subscription.toJSON() as {
    endpoint?: string
    keys?: { p256dh?: string; auth?: string }
  }
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) return null
  return { endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } }
}

/** The browser's current subscription and the body the API stores for it. */
async function currentSubscription(): Promise<{
  subscription: PushSubscription
  body: { endpoint: string; keys: { p256dh: string; auth: string } }
} | null> {
  const reg = await navigator.serviceWorker.getRegistration('/')
  const subscription = await reg?.pushManager.getSubscription()
  if (!subscription) return null
  const body = subscriptionBody(subscription)
  return body ? { subscription, body } : null
}

/** Subscribe this browser against the server's current VAPID public key. */
async function subscribeWithKey(
  reg: ServiceWorkerRegistration,
  publicKey: string,
): Promise<PushSubscription> {
  return reg.pushManager.subscribe({
    // Required by Chrome: a push without a visible notification is an error.
    userVisibleOnly: true,
    applicationServerKey: decodeKey(publicKey),
  })
}

/**
 * Drop the browser's current subscription and create one against `publicKey`.
 *
 * A subscription created against an older VAPID key is dead on arrival: the
 * push service refuses every send with 403, and re-registering the same
 * subscription cannot fix it because the browser keeps handing it back. Only a
 * brand-new subscription picks up the current key. Returns the stored body, or
 * null if the browser refused to subscribe.
 */
async function replaceSubscription(publicKey: string): Promise<PushSubscriptionBody | null> {
  const existing = await currentSubscription()
  await existing?.subscription.unsubscribe().catch(() => undefined)
  const reg = await registration()
  await navigator.serviceWorker.ready
  const fresh = await subscribeWithKey(reg, publicKey)
  const body = subscriptionBody(fresh)
  if (!body) return null
  await api.pushSubscribe(body)
  return body
}

/** The current state, without prompting for anything. */
export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return 'unsupported'
  const current = await currentSubscription()
  const status = await api.pushStatus(current?.body.endpoint).catch(() => null)
  if (!status?.enabled || !status.public_key) return 'unconfigured'
  if (Notification.permission === 'denied') return 'denied'
  if (Notification.permission === 'default') return 'default'
  if (!current) return 'granted'
  // The browser holds a subscription, but the server no longer has the row
  // (pruned after a key rotation, or the endpoint moved to another account on
  // this shared browser). Report it as needing attention rather than claiming
  // the device is subscribed and deliverable.
  return status.endpoint_registered === false ? 'granted' : 'subscribed'
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

  const existing = await currentSubscription()
  if (existing) {
    // A subscription the server still has is re-sent as-is. One it does not have
    // is stale (a pruned row after a key rotation), and turning the toggle back
    // on must replace it rather than register the same dead subscription again.
    const known = await api
      .pushStatus(existing.body.endpoint)
      .then((s) => s.endpoint_registered !== false)
      .catch(() => false)
    if (!known) {
      return (await replaceSubscription(status.public_key)) ? 'subscribed' : 'default'
    }
    await api.pushSubscribe(existing.body)
    return 'subscribed'
  }

  const reg = await registration()
  await navigator.serviceWorker.ready
  const body = subscriptionBody(await subscribeWithKey(reg, status.public_key))
  if (!body) return 'default'
  await api.pushSubscribe(body)
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
 * Re-register on load if this browser is already subscribed, healing two ways a
 * stored subscription goes stale.
 *
 * The common case is cheap: browsers can rotate an endpoint on their own, and a
 * rotated endpoint means every push to the old one is discarded, so the current
 * subscription is re-sent on each authenticated load.
 *
 * The other case is a VAPID key rotation. It invalidates every subscription
 * created before it, and the browser keeps returning that dead subscription, so
 * re-sending it is useless: the push service answers 403 and the server prunes
 * the row. The server then reports the endpoint as unregistered, and the only
 * recovery is to drop the browser's subscription and create a new one against
 * the current key. Without this, a rotation silently stops delivery on every
 * existing device while the settings toggle still reads "subscribed".
 *
 * Concurrent callers share one run. Two effects firing together (StrictMode in
 * development, or two tabs open at once) would otherwise both see the endpoint
 * as unregistered and each subscribe, leaving a duplicate row pointing at a
 * subscription nobody uses.
 */
let syncInFlight: Promise<void> | null = null

export function syncPushSubscription(): Promise<void> {
  if (!syncInFlight) {
    syncInFlight = runSync().finally(() => {
      syncInFlight = null
    })
  }
  return syncInFlight
}

async function runSync(): Promise<void> {
  if (!pushSupported() || Notification.permission !== 'granted') return
  try {
    const current = await currentSubscription()
    const status = await api.pushStatus(current?.body.endpoint).catch(() => null)
    if (!status?.enabled || !status.public_key) return
    if (!current) return

    if (status.endpoint_registered === false) {
      // The server does not have this endpoint, so it can never be delivered
      // to. Replace it with one bound to the current key.
      await replaceSubscription(status.public_key)
      return
    }

    await api.pushSubscribe(current.body)
  } catch {
    // A failed re-sync is not worth surfacing; the next load tries again.
  }
}
