/**
 * Multiple signed-in accounts on one device.
 *
 * Each saved account holds its own access token, so switching profiles is a
 * token swap rather than a fresh sign-in: the account you switch to stays
 * signed in while you use the other one. The active account's token is mirrored
 * into the single `pulse.token` slot that the API client reads, so every
 * existing request path keeps working unchanged and only the session handling
 * here needs to know about more than one account.
 *
 * The tokens live in `localStorage`, which is where the app already keeps its
 * one token. That means every saved session is readable by any script running
 * on the page, so adding accounts widens what a successful XSS would expose.
 * The alternative — an httpOnly cookie — needs a server-side change and is not
 * what this app does today; this module does not make that worse than the
 * single-token case it replaces, but it does multiply it by the number of saved
 * accounts.
 */

import { setToken } from './api'

const ACCOUNTS_KEY = 'pulse.accounts'
const ACTIVE_KEY = 'pulse.activeAccountId'

export interface SavedAccount {
  id: number
  username: string
  display_name: string
  avatar_color: string
  avatar_url: string | null
  token: string
}

function read(): SavedAccount[] {
  try {
    const raw = localStorage.getItem(ACCOUNTS_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Rows written by an older or interrupted version are dropped rather than
    // trusted, so a malformed entry cannot break the switcher for every account.
    return parsed.filter(
      (item): item is SavedAccount =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as SavedAccount).id === 'number' &&
        typeof (item as SavedAccount).username === 'string' &&
        typeof (item as SavedAccount).token === 'string' &&
        Boolean((item as SavedAccount).token),
    )
  } catch {
    return []
  }
}

function write(accounts: SavedAccount[]): void {
  localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(accounts))
}

export function listAccounts(): SavedAccount[] {
  return read()
}

export function getActiveId(): number | null {
  const raw = localStorage.getItem(ACTIVE_KEY)
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

/**
 * Save (or refresh) an account and make it the active one.
 *
 * A sign-in for an account that is already saved replaces that row, so the
 * newest token wins and the list never grows a duplicate for one user.
 */
export function saveAccount(account: SavedAccount): void {
  const accounts = read().filter((item) => item.id !== account.id)
  accounts.push(account)
  write(accounts)
  setActiveId(account.id)
}

/** Make a saved account active by mirroring its token into the API client. */
export function setActiveId(id: number): void {
  localStorage.setItem(ACTIVE_KEY, String(id))
  const account = read().find((item) => item.id === id)
  if (account) setToken(account.token)
}

/**
 * Drop one saved account.
 *
 * When the removed account was active the token slot is cleared, so the caller
 * is left signed out and can switch to another account or sign in again.
 */
export function removeAccount(id: number): void {
  write(read().filter((account) => account.id !== id))
  if (getActiveId() === id) {
    localStorage.removeItem(ACTIVE_KEY)
    setToken(null)
  }
}
