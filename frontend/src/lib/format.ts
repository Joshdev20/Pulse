/** Compact relative time, e.g. "3h", "2d", or a date for older items. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.floor((now - then) / 1000))

  if (seconds < 45) return 'now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d`
  const weeks = Math.floor(days / 7)
  if (weeks < 5) return `${weeks}w`

  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: new Date(iso).getFullYear() === new Date(now).getFullYear() ? undefined : 'numeric',
  })
}

export function formatFullDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export function formatCount(value: number): string {
  if (value < 1000) return String(value)
  if (value < 1_000_000) return `${(value / 1000).toFixed(value % 1000 >= 100 ? 1 : 0)}K`
  return `${(value / 1_000_000).toFixed(1)}M`
}

/** Media length as `m:ss` or `h:mm:ss`, for a video's duration badge. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return ''
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2)
  return parts.map((part) => part[0]?.toUpperCase() ?? '').join('') || '?'
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}

/**
 * Presence line for a direct-message thread header.
 *
 * A live socket wins over the timestamp, because "online" is the more useful
 * answer and the timestamp can be up to a minute stale. "Last seen just now"
 * is deliberately not used for a very recent stamp: it reads as a contradiction
 * when the partner is demonstrably not in the thread.
 */
export function formatPresence(
  iso: string | null | undefined,
  isOnline: boolean | undefined,
  now: number = Date.now(),
): string {
  if (isOnline) return 'Online'
  if (!iso) return 'Offline'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'Offline'
  const seconds = Math.max(0, Math.floor((now - then) / 1000))
  if (seconds < 60) return 'Last seen just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `Last seen ${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `Last seen ${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `Last seen ${days}d ago`
  return `Last seen ${new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
}

/** Split a post body into plain text and hashtag/mention segments for rendering. */
export type Segment =
  | { type: 'text'; value: string }
  | { type: 'tag'; value: string }
  | { type: 'mention'; value: string }

const SEGMENT_RE = /(#[\w]{1,50}|@[A-Za-z0-9_]{3,32})/g

export function segmentBody(body: string): Segment[] {
  const segments: Segment[] = []
  let lastIndex = 0
  for (const match of body.matchAll(SEGMENT_RE)) {
    const index = match.index ?? 0
    if (index > lastIndex) segments.push({ type: 'text', value: body.slice(lastIndex, index) })
    const token = match[0]
    segments.push(
      token.startsWith('#')
        ? { type: 'tag', value: token.slice(1).toLowerCase() }
        : { type: 'mention', value: token.slice(1) },
    )
    lastIndex = index + token.length
  }
  if (lastIndex < body.length) segments.push({ type: 'text', value: body.slice(lastIndex) })
  return segments
}
