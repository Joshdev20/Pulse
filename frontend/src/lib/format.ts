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

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2)
  return parts.map((part) => part[0]?.toUpperCase() ?? '').join('') || '?'
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
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
