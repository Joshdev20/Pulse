import { apiUrl, getToken } from './api'

/**
 * Authenticated media, fetched once and shared.
 *
 * Media lives behind the same bearer auth as the rest of the API, and an
 * `<img src>` cannot send an Authorization header, so the bytes are fetched
 * with `fetch` and handed to the element as an object URL.
 *
 * The cache is module-level rather than per-hook so that several elements
 * showing the same attachment — an avatar beside a post and in its author's
 * row, say — share a single download, and so that a caller can warm an entry
 * before anything renders it (`primeMedia`). Entries are reference counted and
 * released once the last consumer unmounts.
 */
export type MediaAsset = { url: string; filename: string | null }

export type Entry = {
  promise: Promise<MediaAsset>
  refs: number
  asset: MediaAsset | null
  released: boolean
}

/** A failed download, carrying enough for the caller to decide when to retry. */
export class MediaError extends Error {
  status: number
  retryAfterMs: number | null

  constructor(status: number, retryAfterMs: number | null) {
    super(`Media request failed with ${status}`)
    this.name = 'MediaError'
    this.status = status
    this.retryAfterMs = retryAfterMs
  }
}

const cache = new Map<string, Entry>()

/** Pull the server's suggested filename out of Content-Disposition. */
function filenameFrom(header: string | null): string | null {
  if (!header) return null
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header)
  return match ? decodeURIComponent(match[1]) : null
}

async function fetchAsset(path: string, revalidate = false): Promise<MediaAsset> {
  const token = getToken()
  const response = await fetch(apiUrl(path), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    // The bytes for an id are treated as immutable, and the response carries a
    // day-long `max-age` to match. That is wrong for a note the server has
    // since converted in place: the path is unchanged but the bytes are not, so
    // a retry has to ask the server again rather than be answered from the
    // browser's cache. `no-cache` revalidates against the ETag, which changes
    // when the stored file does.
    ...(revalidate ? { cache: 'no-cache' as RequestCache } : {}),
  })
  if (!response.ok) {
    // `Retry-After` is seconds; the rate limiter sets it, so a retry can wait
    // out the window exactly instead of guessing.
    const after = Number(response.headers.get('retry-after'))
    throw new MediaError(response.status, Number.isFinite(after) && after > 0 ? after * 1000 : null)
  }
  const blob = await response.blob()
  return {
    url: URL.createObjectURL(blob),
    filename: filenameFrom(response.headers.get('content-disposition')),
  }
}

function release(entry: Entry) {
  if (entry.asset) {
    URL.revokeObjectURL(entry.asset.url)
    entry.asset = null
  } else {
    // The download is still in flight; revoke it as soon as it lands.
    entry.released = true
  }
}

/**
 * Return the shared entry for `path`, starting the download if needed.
 *
 * The caller owns one reference and must hand it back with `unref`.
 *
 * `revalidate` asks the server rather than the browser cache, for the case
 * where the bytes for a path have changed. Only the caller that is retrying
 * should set it: every other consumer wants the cached copy.
 */
export function refMedia(path: string, revalidate = false): Entry {
  let entry = cache.get(path)
  if (!entry) {
    const created: Entry = {
      promise: fetchAsset(path, revalidate),
      refs: 0,
      asset: null,
      released: false,
    }
    created.promise
      .then((result) => {
        if (created.released) URL.revokeObjectURL(result.url)
        else created.asset = result
      })
      .catch(() => {
        // A failure is not a durable fact. Leaving the rejected entry cached
        // would show a broken image for the rest of the session even after the
        // cause clears — a rate-limited burst, a token that was being refreshed,
        // or a backend that was momentarily down. Drop it so the next consumer
        // gets a fresh attempt.
        if (cache.get(path) === created) cache.delete(path)
      })
    cache.set(path, created)
    entry = created
  }
  entry.refs += 1
  return entry
}

/** Drop one reference, revoking the object URL once nothing holds it. */
export function unrefMedia(path: string, entry: Entry) {
  entry.refs -= 1
  if (entry.refs > 0) return

  // Deferred by a microtask on purpose. React StrictMode tears every effect
  // down and sets it straight back up in the same tick, so releasing
  // synchronously here would cancel a download that is about to be wanted
  // again — the ref goes straight back to 1 before the microtask runs.
  queueMicrotask(() => {
    if (entry.refs > 0 || cache.get(path) !== entry) return
    cache.delete(path)
    release(entry)
  })
}

/**
 * Drop every cached download.
 *
 * Called when the credentials change. The cache is keyed by path, and a path
 * carries no notion of who fetched it, so signing out (or into another
 * account) in the same tab must not leave the previous account's bytes on
 * screen — the avatar and header of whoever was signed in would keep rendering
 * for the next user.
 */
export function clearMediaCache() {
  for (const entry of cache.values()) release(entry)
  cache.clear()
}

/**
 * Drop one path from the cache, so the next consumer downloads it again.
 *
 * For the case where the bytes themselves changed on the server — a stored
 * voice note converted to a playable format, say. The cached object URL holds
 * the old response, and the browser may answer a repeat request from its own
 * cache, so both have to be discarded for a retry to see anything new.
 */
export function dropMedia(path: string) {
  const entry = cache.get(path)
  if (!entry) return
  cache.delete(path)
  release(entry)
}

/**
 * Start a download before anything renders it.
 *
 * This is what lets the browser begin fetching feed media as soon as the feed
 * payload arrives, rather than waiting for each cell to mount and run an
 * effect. The entry is left unclaimed: a consumer that mounts while the fetch
 * is still in flight picks it up, and one that never arrives leaves the result
 * to be discarded rather than leaking an object URL.
 */
export function primeMedia(path: string | null | undefined) {
  if (!path || cache.has(path)) return
  const created: Entry = { promise: fetchAsset(path), refs: 0, asset: null, released: false }
  created.promise
    .then((result) => {
      if (created.refs <= 0) {
        URL.revokeObjectURL(result.url)
        cache.delete(path)
      } else {
        created.asset = result
      }
    })
    .catch(() => cache.delete(path))
  cache.set(path, created)
}
