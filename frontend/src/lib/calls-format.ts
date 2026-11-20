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
