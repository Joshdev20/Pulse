/**
 * The two badge counts the shell shows, as one small external store.
 *
 * The sidebar badge, the mobile badge and the inbox itself all read the same
 * number, and it has to change the instant something is read rather than on the
 * next refetch. A per-component `useState` could not do that: reading a thread
 * happens in `MessagesPage`, the badge is drawn in `Layout`, and a component
 * cannot set another component's state.
 *
 * Deliberately in memory only, like `queryCache`: the numbers are one account's
 * view of the world, so they must not survive a sign-out on a shared device.
 */

type Kind = 'messages' | 'notifications'

interface Counts {
  messages: number
  notifications: number
}

let counts: Counts = { messages: 0, notifications: 0 }
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

export function subscribeCounts(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function readCounts(): Counts {
  return counts
}

/** Replace a count with the server's answer. */
export function setCount(kind: Kind, value: number): void {
  const next = Math.max(0, Math.round(value))
  if (counts[kind] === next) return
  counts = { ...counts, [kind]: next }
  emit()
}

/**
 * Subtract from a count locally, without waiting for a request.
 *
 * This is what makes a thread or a notification look read the moment it is
 * opened: the badge drops now, and the server's number replaces it when the
 * call lands. Clamped at zero so a double-open cannot show a negative badge.
 */
export function decrement(kind: Kind, by = 1): void {
  if (by <= 0) return
  setCount(kind, Math.max(0, counts[kind] - by))
}

export function resetCounts(): void {
  counts = { messages: 0, notifications: 0 }
  emit()
}
