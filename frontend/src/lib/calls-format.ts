import type { CallLog } from './types'

/**
 * How a call is worded for the reader.
 *
 * Kept in one place because the same call has to read consistently in the
 * thread entry, the inbox preview and a notification: "Missed" in one and "No
 * answer" in another looks like two different events.
 *
 * The server resolves the stored state per reader, so this module only picks
 * words — it never re-derives who called whom.
 */

/** "4m 05s" / "12s" — compact enough for a one-line preview. */
export function durationText(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
  }
  if (minutes > 0) return `${minutes}m ${String(rest).padStart(2, '0')}s`
  return `${rest}s`
}

/** The main line of a call entry, from the reader's side. */
export function callLabel(call: CallLog): string {
  const video = call.mode === 'video'
  const kind = video ? 'Video call' : 'Voice call'
  switch (call.state) {
    case 'ringing':
      return call.by_me ? `Calling…` : `Incoming ${kind.toLowerCase()}`
    case 'active':
      return kind
    case 'missed':
      return call.by_me ? `No answer` : `Missed ${kind.toLowerCase()}`
    case 'declined':
      return call.by_me ? `Declined` : `You declined`
    case 'cancelled':
      return 'Cancelled'
    case 'answered':
      return kind
  }
}

/**
 * The second line: a duration for a call that connected, a direction for one
 * that did not.
 *
 * A connected call's duration is the fact worth showing; for one that never
 * connected the useful fact is who called, so the two cases carry different
 * information rather than repeating the label.
 */
export function callDetail(call: CallLog): string | null {
  if (call.state === 'answered' && call.seconds !== null) {
    return durationText(call.seconds)
  }
  if (call.state === 'active' || call.state === 'ringing') return null
  return call.by_me ? 'Outgoing' : 'Incoming'
}

/** A single line for an inbox row or a notification. */
export function callPreview(call: CallLog): string {
  const video = call.mode === 'video'
  const kind = video ? 'Video call' : 'Voice call'
  switch (call.state) {
    case 'ringing':
      return call.by_me ? `Calling…` : `Incoming ${kind.toLowerCase()}`
    case 'active':
      return `Ongoing ${kind.toLowerCase()}`
    case 'missed':
      return call.by_me ? `No answer` : `Missed ${kind.toLowerCase()}`
    case 'declined':
      return call.by_me ? `Call declined` : `You declined a ${kind.toLowerCase()}`
    case 'cancelled':
      return `Cancelled ${kind.toLowerCase()}`
    case 'answered':
      return call.seconds !== null
        ? `${kind} · ${durationText(call.seconds)}`
        : kind
  }
}

/**
 * How long a call may read as live on screen before it is shown as over.
 *
 * The server resolves a stale row on read and the sweeper finalises it within
 * `CALL_SWEEP_INTERVAL_SECONDS`, but a thread that is already open only learns
 * about that from a pushed frame. If that frame is lost — the peer closed the
 * tab, the socket blipped — the entry sat as "Calling…" until the reader
 * navigated away and back, which is the lag this bounds.
 *
 * Kept just above the server's own ring expiry so the two agree: a call the
 * server still considers live is not shown as missed here first.
 */
const RING_STALE_MS = 90_000
// Matches the server's `ACTIVE_EXPIRY_SECONDS`. Both are a backstop for a row
// whose final hangup was lost, so they only need to be longer than any call
// that could really still be running.
const ACTIVE_STALE_MS = 6 * 60 * 60 * 1000

/**
 * The state to *display* for a call, given when it started.
 *
 * Display only: nothing is written back, so a call that really is still ringing
 * recovers as soon as the next frame arrives. This is the client's half of the
 * same rule the server applies on read, and it exists because a client that
 * renders a two-hour-old "Calling…" is showing a call that cannot be happening.
 */
export function displayCallState(call: CallLog, createdAt: string, now: number): CallLog['state'] {
  if (call.state !== 'ringing' && call.state !== 'active') return call.state
  const started = new Date(createdAt).getTime()
  if (Number.isNaN(started)) return call.state
  const age = now - started
  if (call.state === 'ringing' && age > RING_STALE_MS) return 'cancelled'
  if (call.state === 'active' && age > ACTIVE_STALE_MS) return 'answered'
  return call.state
}
