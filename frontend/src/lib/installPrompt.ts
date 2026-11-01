/**
 * Offering to install Pulse as an app.
 *
 * Android and iOS need opposite treatment, and neither can be handled the way
 * the other is. Chrome on Android fires `beforeinstallprompt`, which can be
 * deferred and replayed later from our own button. iOS Safari has no such event
 * and never will — Apple requires the manual Share > Add to Home Screen route —
 * so there the only honest thing is to explain the steps.
 *
 * The event is captured at module scope rather than inside a component because
 * it can fire before React has mounted. A listener added in an effect would miss
 * it on a fast load and the button would never appear.
 */

/** Chrome's deferred install event, which TypeScript's DOM lib does not declare. */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export type InstallPlatform = 'android' | 'ios' | 'other'

const DISMISS_KEY = 'pulse.install.dismissed'

/** How long a "Not now" lasts before the offer returns. */
const SNOOZE_MS = 30 * 24 * 60 * 60 * 1000

/** Wait this long before offering, so it never competes with the first paint. */
export const OFFER_DELAY_MS = 4000

let deferredPrompt: BeforeInstallPromptEvent | null = null
const listeners = new Set<() => void>()

function announce(): void {
  listeners.forEach((listener) => listener())
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    // Held back deliberately: without this Chrome shows its own mini-infobar,
    // which we cannot style, place or dismiss on the user's behalf.
    event.preventDefault()
    deferredPrompt = event as BeforeInstallPromptEvent
    announce()
  })

  window.addEventListener('appinstalled', () => {
    // Installed for real. Forget any earlier "not now" so a future device or
    // browser does not inherit a dismissal from this one.
    deferredPrompt = null
    clearDismissal()
    announce()
  })
}

/** Which install path applies to this browser. */
export function installPlatform(): InstallPlatform {
  if (typeof navigator === 'undefined') return 'other'
  const ua = navigator.userAgent
  // iPadOS 13 and later report a desktop "Macintosh" user agent, so the touch
  // point count is the only reliable tell that it is really an iPad.
  const iPadOS = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1
  if (/iPhone|iPad|iPod/.test(ua) || iPadOS) return 'ios'
  if (/Android/.test(ua)) return 'android'
  return 'other'
}

/**
 * Whether Pulse is already running as an installed app.
 *
 * Both checks are needed: `display-mode` covers Android and desktop, while
 * `navigator.standalone` is the older iOS-only flag.
 */
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false
  const standalone = (navigator as Navigator & { standalone?: boolean }).standalone === true
  return standalone || window.matchMedia('(display-mode: standalone)').matches
}

/**
 * iOS browsers other than Safari cannot add to the home screen.
 *
 * All of them run WebKit, so they are distinguishable only by their own tokens:
 * `CriOS` is Chrome, `FxiOS` Firefox and `EdgiOS` Edge.
 */
export function isIOSSafari(): boolean {
  if (installPlatform() !== 'ios') return false
  return !/CriOS|FxiOS|EdgiOS|OPiOS/.test(navigator.userAgent)
}

/** True while the native Android prompt is available to replay. */
export function promptAvailable(): boolean {
  return deferredPrompt !== null
}

/** Subscribe to changes in prompt availability, for `useSyncExternalStore`. */
export function subscribePrompt(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function clearDismissal(): void {
  try {
    localStorage.removeItem(DISMISS_KEY)
  } catch {
    // Private browsing can refuse storage. A prompt shown again is a far smaller
    // problem than a crash, so the failure is swallowed.
  }
}

/** Record "not now" so the offer stays away for a while. */
export function dismissInstall(): void {
  try {
    localStorage.setItem(DISMISS_KEY, String(Date.now()))
  } catch {
    // See clearDismissal.
  }
}

/** True if the user declined recently and should not be asked again yet. */
export function dismissedRecently(): boolean {
  try {
    const raw = localStorage.getItem(DISMISS_KEY)
    if (!raw) return false
    const at = Number(raw)
    if (!Number.isFinite(at)) return false
    return Date.now() - at < SNOOZE_MS
  } catch {
    // Storage unavailable means we cannot know they declined. Offering once is
    // the better failure than never offering at all.
    return false
  }
}

/**
 * Show Chrome's install dialog and report what the user chose.
 *
 * The deferred event is single-use: replaying it a second time does nothing, so
 * it is dropped once spent rather than left to make a dead button.
 */
export async function triggerInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const event = deferredPrompt
  if (!event) return 'unavailable'
  deferredPrompt = null
  announce()
  await event.prompt()
  const choice = await event.userChoice
  return choice.outcome
}
