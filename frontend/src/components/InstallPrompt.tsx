import { useEffect, useState, useSyncExternalStore } from 'react'
import { PulseMark } from './PulseLogo'
import { ShareIcon } from './Icons'
import {
  OFFER_DELAY_MS,
  dismissInstall,
  dismissedRecently,
  installPlatform,
  isIOSSafari,
  isStandalone,
  promptAvailable,
  subscribePrompt,
  triggerInstall,
} from '../lib/installPrompt'

/**
 * A dismissible card inviting the user to install Pulse.
 *
 * Shown only to someone who is still using Pulse in a browser tab: an installed
 * app has nothing to install, and a desktop browser has no home screen, so both
 * are excluded. A hidden tab is excluded too — the offer waits for a moment when
 * the user is actually looking at it.
 *
 * The two platforms get different cards because the capability differs. Android
 * can install in one tap, so it gets a button. iOS cannot be triggered from the
 * page at all, so it gets the Share icon and the two taps that do work.
 */
export function InstallPrompt() {
  const platform = installPlatform()
  const [visible, setVisible] = useState(false)
  // Read through useSyncExternalStore because the event is captured at module
  // scope: the prompt may already have arrived before this mounted.
  const canPrompt = useSyncExternalStore(subscribePrompt, promptAvailable, () => false)
  const [busy, setBusy] = useState(false)
  const [installed, setInstalled] = useState(false)

  useEffect(() => {
    if (installed || isStandalone() || dismissedRecently()) return
    // Nothing to offer a desktop browser, and an iOS browser other than Safari
    // has no Add to Home Screen at all.
    if (platform === 'other') return
    if (platform === 'ios' && !isIOSSafari()) return

    let timer = window.setTimeout(() => {
      // The user may have installed Pulse or wandered off during the delay.
      if (!isStandalone() && !document.hidden) setVisible(true)
    }, OFFER_DELAY_MS)

    // The delay exists so the offer never lands on the first paint. A user who
    // leaves and comes back is settled in, so the next return shows it at once.
    const onVisibility = () => {
      if (document.hidden) return
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        if (!isStandalone()) setVisible(true)
      }, 1500)
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [platform, installed])

  if (!visible || installed) return null

  const close = () => {
    dismissInstall()
    setVisible(false)
  }

  const install = async () => {
    setBusy(true)
    try {
      const outcome = await triggerInstall()
      // A dismissal of the native dialog is not a refusal of the feature, so it
      // is not recorded as one: the card stays put and the button can be used
      // again. Only the card's own "Not now" is treated as a decision.
      if (outcome === 'accepted') {
        setInstalled(true)
        setVisible(false)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="install-prompt" role="dialog" aria-label="Install Pulse">
      <PulseMark size={38} className="install-prompt-mark" />
      <div className="install-prompt-text">
        <strong>Install Pulse</strong>
        {platform === 'ios' ? (
          <p>
            Tap <ShareIcon size={15} className="install-prompt-icon" /> Share, then{' '}
            <strong>Add to Home Screen</strong> to open Pulse full screen, straight from your home
            screen.
          </p>
        ) : (
          <p>Add Pulse to your home screen and it opens full screen, without the browser bars.</p>
        )}
      </div>
      <div className="install-prompt-actions">
        {platform === 'android' && canPrompt && (
          <button type="button" className="btn btn-primary btn-sm" onClick={install} disabled={busy}>
            {busy ? 'Installing…' : 'Install'}
          </button>
        )}
        {platform === 'android' && !canPrompt && (
          <p className="install-prompt-hint">
            Open your browser menu and choose <strong>Install app</strong>.
          </p>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={close}>
          Not now
        </button>
      </div>
    </aside>
  )
}
