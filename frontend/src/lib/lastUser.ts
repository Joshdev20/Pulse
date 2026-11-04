import type { User } from './types'

/**
 * The last session's own profile, kept so a returning visitor does not stare at
 * the loading mark.
 *
 * The boot sequence must confirm the token with `/auth/me` before it can trust
 * the session, and on a free host that request can take a second. Rendering the
 * app from this cached copy while that check runs means a repeat visitor sees
 * their feed immediately and the check becomes a background correction.
 *
 * It is only ever a display copy of the viewer's *own* profile, which they can
 * already see. Nothing is authorised from it: every request still carries the
 * real token, and a token the server rejects clears both this and the session.
 */
const KEY = 'pulse.lastUser'

export function saveLastUser(user: User): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(user))
  } catch {
    // A full or unavailable storage is not worth failing a sign-in over; the
    // app simply falls back to showing the loader next time.
  }
}

export function loadLastUser(): User | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as User
    // Guard the one field the app dereferences unconditionally. A payload
    // written by an older build, or corrupted, is discarded rather than
    // rendered as a half-empty profile.
    if (typeof parsed?.id === 'number' && typeof parsed?.username === 'string') return parsed
    return null
  } catch {
    return null
  }
}

export function clearLastUser(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // Nothing to do: the session is being torn down regardless.
  }
}
