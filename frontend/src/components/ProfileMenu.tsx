import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AccountList } from './AccountList'
import { BellIcon, EditIcon, LogoutIcon, MenuIcon, MoonIcon, SunIcon } from './Icons'
import { Dropdown } from './UI'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { isSoundEnabled, playNotificationSound, setSoundEnabled } from '../lib/sound'
import { disablePush, enablePush, pushState, pushSupported } from '../lib/push'
import type { PushState } from '../lib/push'
import { useTheme } from '../lib/useTheme'

/** The explanatory line under the push toggle, per state. */
function pushSubtitle(state: PushState): string {
  switch (state) {
    case 'unsupported':
      return 'This browser does not support push notifications.'
    case 'unconfigured':
      return 'Push notifications are not configured on the server.'
    case 'denied':
      return 'Blocked in your browser settings. Allow notifications for Pulse to turn this on.'
    case 'subscribed':
      return 'Alerts arrive even when Pulse is closed.'
    case 'granted':
      // Permission is already allowed, so turning this on will not prompt.
      return 'Permission is allowed. Turn this on to get alerts when Pulse is closed.'
    default:
      return 'Get alerts on this device even when Pulse is closed.'
  }
}

/**
 * The profile menu behind the hamburger on your own profile.
 *
 * It holds everything about the account and the device — appearance, the
 * notification preferences, the saved-account switcher and signing out — so the
 * profile page itself can keep only what edits the profile. These are the
 * controls that used to sit on the Settings screen beside the profile fields.
 */
export function ProfileMenu() {
  const { user, logout } = useAuth()
  const { notify } = useToast()
  const { theme, setTheme } = useTheme()
  const [sound, setSound] = useState(() => isSoundEnabled())
  const [push, setPush] = useState<PushState>(() => (pushSupported() ? 'default' : 'unsupported'))
  const [pushBusy, setPushBusy] = useState(false)

  const pushOn = push === 'subscribed'

  // Read the real push state when the menu opens, and again whenever the active
  // account changes.
  //
  // The read has to outlive the effect's own cleanup. Opening the menu flips
  // `opened`, which both starts this read and re-runs the effect, so the
  // cleanup of the previous run fires while the read is still in flight. That
  // cleanup used to set a `cancelled` flag that discarded the result, leaving
  // the toggle on its initial "off" no matter what the browser reported. The
  // flag is now only a guard against the unmount case.
  //
  // `user?.id` in the deps is what makes a switch to another account re-read
  // instead of leaving the previous account's answer on screen.
  const [opened, setOpened] = useState(false)
  useEffect(() => {
    if (!opened) return
    let active = true
    void pushState().then((state) => {
      if (active) setPush(state)
    })
    return () => {
      active = false
    }
  }, [opened, user?.id])

  // A switch to another account must not keep showing the account just left
  // while the fresh read is in flight.
  useEffect(() => {
    setPush(pushSupported() ? 'default' : 'unsupported')
  }, [user?.id])

  if (!user) return null

  const togglePush = async (next: boolean) => {
    setPushBusy(true)
    try {
      const state = next ? await enablePush() : await disablePush()
      setPush(state)
      if (next && state === 'subscribed') notify('Push notifications enabled')
      else if (next && state === 'denied') notify('Notifications are blocked in your browser', 'error')
      else if (next && state === 'unconfigured') notify('Push notifications are not configured', 'error')
      else if (!next) notify('Push notifications disabled')
    } catch {
      notify('Could not change push notifications', 'error')
    } finally {
      setPushBusy(false)
    }
  }

  return (
    <Dropdown
      onOpenChange={setOpened}
      trigger={() => (
        <span className="profile-menu-trigger" aria-label="Profile menu">
          <MenuIcon size={20} />
        </span>
      )}
    >
      {(close) => (
        <div className="profile-menu">
          <Link to="/settings" className="menu-item" onClick={close}>
            <EditIcon size={16} /> Edit profile
          </Link>

          <div className="profile-menu-head">Appearance</div>
          <div className="profile-menu-theme" role="radiogroup" aria-label="Theme">
            <button
              type="button"
              role="radio"
              aria-checked={theme === 'light'}
              className={`theme-option${theme === 'light' ? ' active' : ''}`}
              onClick={() => setTheme('light')}
            >
              <SunIcon size={16} />
              <span>Light</span>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={theme === 'dark'}
              className={`theme-option${theme === 'dark' ? ' active' : ''}`}
              onClick={() => setTheme('dark')}
            >
              <MoonIcon size={16} />
              <span>Dark</span>
            </button>
          </div>

          <label className="switch-row profile-menu-row">
            <span className="switch-row-text">
              <span className="profile-menu-label">Notification sound</span>
              <span className="profile-menu-sub">Play a chime when a message or alert arrives.</span>
            </span>
            <input
              type="checkbox"
              className="switch-input"
              checked={sound}
              onChange={(event) => {
                const next = event.target.checked
                setSound(next)
                setSoundEnabled(next)
                // Preview the chime when turning it on, so the choice is
                // audible at the moment it is made.
                if (next) playNotificationSound()
              }}
            />
            <span className="switch" aria-hidden="true">
              <BellIcon size={14} />
            </span>
          </label>

          <label className="switch-row profile-menu-row">
            <span className="switch-row-text">
              <span className="profile-menu-label">Push notifications</span>
              <span className="profile-menu-sub">{pushSubtitle(push)}</span>
            </span>
            <input
              type="checkbox"
              className="switch-input"
              checked={pushOn}
              disabled={pushBusy || push === 'unsupported' || push === 'unconfigured'}
              onChange={(event) => void togglePush(event.target.checked)}
            />
            <span className="switch" aria-hidden="true">
              <BellIcon size={14} />
            </span>
          </label>

          <div className="profile-menu-head">Accounts</div>
          <AccountList onClose={close} />

          <button
            type="button"
            className="menu-item danger profile-menu-signout"
            onClick={() => {
              close()
              logout()
            }}
          >
            <LogoutIcon size={16} /> Sign out of @{user.username}
          </button>
        </div>
      )}
    </Dropdown>
  )
}
