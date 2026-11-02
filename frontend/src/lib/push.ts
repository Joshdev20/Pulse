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
import { isStandalone, installPlatform } from './installPrompt'
import type { PushSubscriptionBody } from './types'

export type PushState =
  | 'unsupported'
  | 'unconfigured'
  | 'needs-install'
  | 'default'
  | 'granted'
  | 'denied'
  | 'subscribed'

/**
 * The last state that was computed, so a freshly mounted settings menu can show
 * the truth at once instead of flashing "off" and correcting itself a moment
 * later. Session-scoped; cleared when the signed-in account changes, because
 * the answer belongs to the account.
 */
let cachedState: PushState | null = null

/** The last known state, or null if none has been computed yet this session. */
export function cachedPushState(): PushState | null {
  return cachedState
}

/** Forget the cached state, for when the answer no longer applies. */
export function clearCachedPushState(): void {
  cachedState = null
}

function remember(state: PushState): PushState {
  cachedState = state
  return state
}

/**
 * The user's own decision for an account: 'on' once they have turned push on,
 * 'off' once they have turned it off.
 *
 * Kept in localStorage, keyed by account, because the decision is the user's and
 * not a fact the server can supply. Everything else here is derived from live
 * reads that can fail or race; without this record the switch has no memory, so
 * it renders off on every mount and can be flipped back on by a background sync
 * the user never asked for. Persisting it is what makes the choice stick.
 */
function choiceKey(userId?: number): string {
  return userId === undefined ? 'pulse.push' : `pulse.push.${userId}`
}

export function storedPushChoice(userId?: number): 'on' | 'off' | null {
  try {
    const value = localStorage.getItem(choiceKey(userId))
    return value === 'on' || value === 'off' ? value : null
  } catch {
    return null
  }
}

function rememberChoice(userId: number | undefined, choice: 'on' | 'off'): void {
  try {
    localStorage.setItem(choiceKey(userId), choice)
  } catch {
    // Nothing to persist; the in-memory cache still holds this session's answer.
  }
}

/**
 * Whether the switch should read as on. This is the user's recorded decision and
 * nothing else.
 *
 * The switch position must not be derived from a live read: a read can fail, can
 * arrive late, or can be triggered by the app itself, and any of those would move
 * a control the user set. Only `enablePush` and `disablePush` — which run because
 * the user moved the switch — may change this.
 */
export function pushChoiceOn(userId?: number): boolean {
  return storedPushChoice(userId) === 'on'
}

/**
 * Record the browser's existing subscription as the decision, once, for someone
 * who has never made one.
 *
 * Only a real subscription is adopted, and only as on. A *missing* subscription
 * is deliberately not recorded as off: the service worker may simply not be
 * registered yet on a fresh load, and writing 'off' there would switch off a
 * device the user never touched — the very thing this module exists to prevent.
 * Absence of a decision already reads as off, so nothing is lost by waiting.
 */
export function adoptPushChoice(userId: number | undefined, state: PushState): void {
  if (storedPushChoice(userId) !== null) return
  if (state === 'subscribed') rememberChoice(userId, 'on')
}

/**
 * The state to show the instant the menu opens, before any request has answered.
 *
 * The on/off decision comes from the recorded choice, so the subtitle matches the
 * switch on the first paint. `unconfigured`/`unsupported` are the exceptions: they
 * are worth showing straight away because they explain why the switch cannot be
 * used at all.
 */
export function initialPushState(userId?: number): PushState {
  if (!pushSupported()) return 'unsupported'
  if (needsInstallForPush()) return 'needs-install'
  const cached = cachedPushState()
  if (cached === 'unconfigured' || cached === 'unsupported' || cached === 'needs-install') return cached
  const permission = notificationPermission()
  if (permission === 'denied') return 'denied'
  if (pushChoiceOn(userId)) return 'subscribed'
  return permission === 'granted' ? 'granted' : 'default'
}

/**
 * The best answer available when the server cannot be reached.
 *
 * A failed status read is not the same as "push is switched off at the server".
 * Reporting it as `unconfigured` both disabled the switch and told the user the
 * server was misconfigured, when the real cause was a dropped request — which
 * on a free-tier host that cold-starts is routine. Fall back to the browser and
 * the stored decision instead.
 */
function unreachableState(userId?: number): PushState {
  const permission = notificationPermission()
  if (permission === 'denied') return 'denied'
  if (pushChoiceOn(userId)) return 'subscribed'
  return permission === 'granted' ? 'granted' : 'default'
}

/** Whether this browser can do Web Push at all. */
export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/**
 * Whether this iPhone is running Pulse in a Safari tab rather than installed.
 *
 * iOS grants Web Push to an installed home screen app only. A Safari tab on the
 * same phone reports `PushManager`, `Notification` and a service worker, so every
 * capability check passes and the toggle looks usable — but subscribing either
 * throws or yields an endpoint that never delivers. Checking this first lets the
 * UI ask the user to install instead of offering a switch that cannot work.
 *
 * Deliberately limited to iOS: Android and desktop deliver to an ordinary tab,
 * so gating them the same way would withhold a feature that does work.
 */
export function needsInstallForPush(): boolean {
  return installPlatform() === 'ios' && !isStandalone()
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
export async function pushState(userId?: number): Promise<PushState> {
  if (!pushSupported()) return remember('unsupported')
  // An iPhone in a Safari tab cannot receive push at all, so say that before
  // asking the server or the browser anything: both would report a healthy
  // subscription that can never be delivered to.
  if (needsInstallForPush()) return remember('needs-install')
  // Report the last known answer while a replacement is in progress. Reading
  // the browser mid-heal would see the old subscription already dropped and
  // report "off" for a device that is only being renewed.
  const settling = settleInFlight
  if (settling) {
    try {
      await settling
    } catch {
      // A failed heal leaves the cached answer in place; the next read corrects it.
    }
  }
  const current = await currentSubscription()
  // A request that fails (a cold start, a dropped connection) must not be read
  // as "push is off at the server": that would disable the switch and blame the
  // configuration. Only a real answer can say `enabled: false`.
  const status = await api.pushStatus(current?.body.endpoint).catch(() => undefined)
  if (status === undefined) return remember(unreachableState(userId))
  if (!status.enabled || !status.public_key) return remember('unconfigured')
  if (Notification.permission === 'denied') return remember('denied')
  if (Notification.permission === 'default') return remember('default')
  if (!current) return remember(storedPushChoice(userId) === 'on' ? 'granted' : 'default')
  // The browser holds a subscription, but the server no longer has the row
  // (pruned after a key rotation, or the endpoint moved to another account on
  // this shared browser). Report it as needing attention rather than claiming
  // the device is subscribed and deliverable.
  return remember(status.endpoint_registered === false ? 'granted' : 'subscribed')
}

/**
 * Register, ask permission, and subscribe. Returns the resulting state.
 *
 * Must be called from a user gesture: browsers refuse the permission prompt
 * otherwise. Every failure is reported as a state rather than thrown, so the
 * settings screen can show what happened instead of breaking.
 */
export async function enablePush(userId?: number): Promise<PushState> {
  if (!pushSupported()) return remember('unsupported')
  // Refuse before touching the permission prompt. Asking an iPhone user in a
  // Safari tab to allow notifications would grant permission that can never
  // deliver anything, leaving them with a "granted" toggle and no alerts.
  if (needsInstallForPush()) return remember('needs-install')
  const status = await api.pushStatus().catch(() => undefined)
  // A failed read is not a reason to refuse: the user asked for this, so carry
  // on and let the subscribe call be the thing that fails if the server is down.
  if (status !== undefined && (!status.enabled || !status.public_key)) {
    return remember('unconfigured')
  }

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return remember(permission === 'denied' ? 'denied' : 'default')

  const publicKey = status?.public_key
  if (!publicKey) return remember('unconfigured')

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
      const state = (await replaceSubscription(publicKey)) ? 'subscribed' : 'default'
      if (state === 'subscribed') rememberChoice(userId, 'on')
      return remember(state)
    }
    await api.pushSubscribe(existing.body)
    rememberChoice(userId, 'on')
    return remember('subscribed')
  }

  const reg = await registration()
  await navigator.serviceWorker.ready
  const body = subscriptionBody(await subscribeWithKey(reg, publicKey))
  if (!body) return remember('default')
  await api.pushSubscribe(body)
  rememberChoice(userId, 'on')
  return remember('subscribed')
}

/** Cancel the browser subscription and forget it on the server. */
export async function disablePush(userId?: number): Promise<PushState> {
  if (!pushSupported()) return remember('unsupported')
  const reg = await navigator.serviceWorker.getRegistration('/')
  const subscription = await reg?.pushManager.getSubscription()
  if (subscription) {
    // Tell the server first: once the browser subscription is gone we no longer
    // have the endpoint to send, and the row would linger forever.
    await api.pushUnsubscribe(subscription.endpoint).catch(() => undefined)
    await subscription.unsubscribe().catch(() => undefined)
  }
  // The user's decision is recorded even if the network calls above failed: the
  // browser subscription is gone either way, and this is what stops the next
  // load's background sync from quietly turning push back on.
  rememberChoice(userId, 'off')
  return remember(notificationPermission() === 'granted' ? 'granted' : 'default')
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

/**
 * The account the in-flight sync belongs to, so a run started for one account is
 * not reused for another.
 */
let syncUserId: number | undefined

/**
 * The heal currently replacing a stale subscription, if any.
 *
 * A replacement drops the browser's subscription before creating the new one,
 * so for a few hundred milliseconds there is genuinely no subscription. A read
 * that lands in that window would report "off" for a device that is merely
 * being renewed. Readers await this instead.
 */
let settleInFlight: Promise<void> | null = null

export function syncPushSubscription(userId?: number): Promise<void> {
  if (!syncInFlight || syncUserId !== userId) {
    const run = runSync(userId).finally(() => {
      if (syncInFlight === run) {
        syncInFlight = null
        settleInFlight = null
      }
    })
    syncInFlight = run
    syncUserId = userId
    settleInFlight = run
  }
  return syncInFlight
}

async function runSync(userId?: number): Promise<void> {
  if (!pushSupported()) {
    remember('unsupported')
    return
  }
  if (Notification.permission === 'denied') {
    remember('denied')
    return
  }
  // Not granted: there is nothing to sync, and the switch's position comes from
  // the recorded choice, so only the subtitle needs the browser's answer.
  if (Notification.permission !== 'granted') {
    remember(pushChoiceOn(userId) ? 'subscribed' : 'default')
    return
  }
  try {
    // This runs on every authenticated load, so it must never change the switch:
    // it reports the recorded choice and, at most, repairs delivery underneath
    // it. Nothing here may flip the switch on or off — the user does that.
    // Register first: a fresh load may not have a worker yet, and without it the
    // subscription check below would report "none" and misread an existing
    // subscriber as someone who never opted in.
    await registration().catch(() => undefined)
    const current = await currentSubscription()
    const status = await api.pushStatus(current?.body.endpoint).catch(() => undefined)
    if (status === undefined) {
      remember(unreachableState(userId))
      return
    }
    if (!status.enabled || !status.public_key) {
      // The server genuinely cannot send. Say so, which disables the switch, so
      // the user is not left thinking alerts will arrive.
      remember('unconfigured')
      return
    }

    // No decision yet: adopt whatever the browser already holds, once. An
    // existing subscriber is recorded as on rather than being shown as off and
    // left to wonder why alerts stopped.
    if (storedPushChoice(userId) === null) {
      adoptPushChoice(userId, current ? 'subscribed' : 'granted')
    }

    if (!pushChoiceOn(userId)) {
      // The user chose off, so there is nothing to maintain. Reporting the choice
      // keeps the switch off, which is what they asked for.
      remember('granted')
      return
    }

    if (!current) {
      // Chose on, permission granted, but the browser has no subscription: a
      // reinstalled Home Screen app, cleared site data, or an endpoint the
      // browser dropped. Their choice still stands, so rebuild the subscription
      // rather than leaving alerts silently broken. Nothing is prompted for.
      const body = subscriptionBody(await subscribeWithKey(await registration(), status.public_key))
      if (body) {
        await api.pushSubscribe(body)
        remember('subscribed')
      } else {
        remember('granted')
      }
      return
    }

    if (status.endpoint_registered === false) {
      // The server does not have this endpoint, so it can never be delivered
      // to. Replace it with one bound to the current key.
      remember((await replaceSubscription(status.public_key)) ? 'subscribed' : 'granted')
      return
    }

    await api.pushSubscribe(current.body)
    remember('subscribed')
  } catch {
    // A failed re-sync is not worth surfacing; the next load tries again.
  }
}
