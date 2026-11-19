/**
 * A small in-memory cache for API reads, so navigating back to a screen paints
 * what was there instead of fetching it again.
 *
 * Every page used to load its data on mount and show a skeleton while it did.
 * On this deployment a single read costs well over a second, so leaving a post
 * and coming back meant watching the feed reload — and losing the scroll
 * position with it. A reader who steps into a post and returns has not asked
 * for fresh data; they asked to go back.
 *
 * Deliberately in memory only, never localStorage: the entries are another
 * account's view of the world as much as this one's, and a cache that outlives
 * the session is a cache that can show a signed-out visitor someone else's feed.
 * Clearing on sign-out is the guarantee, and keeping it in memory makes that
 * guarantee hold even if a clear is missed.
 */

interface Entry {
  value: unknown
  /** When the value was fetched, for deciding whether it is stale. */
  at: number
  /** Present while a fetch is in flight, so parallel callers share it. */
  inFlight?: Promise<unknown>
}

const entries = new Map<string, Entry>()

/** How long a value is served without any refresh. */
export const DEFAULT_TTL_MS = 60_000

/** Return a cached value, or undefined when there is nothing for that key. */
export function read<T>(key: string): T | undefined {
  return entries.get(key)?.value as T | undefined
}

/**
 * Whether a cached value is recent enough to trust without revalidating.
 *
 * A miss and an expired entry are the same answer here: both mean "go and ask".
 */
export function isFresh(key: string, ttlMs: number = DEFAULT_TTL_MS): boolean {
  const entry = entries.get(key)
  if (!entry) return false
  return Date.now() - entry.at < ttlMs
}

export function write<T>(key: string, value: T): void {
  entries.set(key, { value, at: Date.now() })
}

/**
 * Read through the cache, fetching when there is no fresh value.
 *
 * Concurrent callers for the same key share one request, which matters on mount
 * when a page and a widget both want the same data. A failed fetch leaves any
 * older value in place rather than deleting it: stale data that the user can
 * still read beats an error screen over a network blip.
 *
 * Pass `force` after an action that changes the data behind the key. The write
 * that just happened makes the cached copy provably wrong, so serving it would
 * show the reader the state from before their own action.
 */
export async function load<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs: number = DEFAULT_TTL_MS,
  force = false,
): Promise<T> {
  const entry = entries.get(key)
  if (entry && !force) {
    if (entry.inFlight) return entry.inFlight as Promise<T>
    if (Date.now() - entry.at < ttlMs) return entry.value as T
  }

  const promise = fetcher()
    .then((value) => {
      entries.set(key, { value, at: Date.now() })
      return value
    })
    .catch((caught) => {
      const current = entries.get(key)
      if (current) entries.set(key, { value: current.value, at: current.at })
      else entries.delete(key)
      throw caught
    })
    .finally(() => {
      const current = entries.get(key)
      if (current?.inFlight === promise) delete current.inFlight
    })

  entries.set(key, { value: entry?.value, at: entry?.at ?? 0, inFlight: promise })
  return promise
}

/** Drop every key beginning with ``prefix``. */
export function invalidate(prefix: string): void {
  for (const key of [...entries.keys()]) {
    if (key.startsWith(prefix)) entries.delete(key)
  }
}

/** Drop everything. Called on sign-out and account switch. */
export function clear(): void {
  entries.clear()
}
