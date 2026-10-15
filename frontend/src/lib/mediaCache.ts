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

type Entry = {
  promise: Promise<MediaAsset>
  refs: number
  asset: MediaAsset | null
  released: boolean
}

const cache = new Map<string, Entry>()

/** Pull the server's suggested filename out of Content-Disposition. */
function filenameFrom(header: string | null): string | null {
  if (!header) return null
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header)
  return match ? decodeURIComponent(match[1]) : null
}

async function fetchAsset(path: string): Promise<MediaAsset> {
  const token = getToken()
  const response = await fetch(apiUrl(path), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
  if (!response.ok) throw new Error(`Media request failed with ${response.status}`)
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
 */
export function refMedia(path: string): Entry {
  let entry = cache.get(path)
  if (!entry) {
    const created: Entry = { promise: fetchAsset(path), refs: 0, asset: null, released: false }
    created.promise
      .then((result) => {
        if (created.released) URL.revokeObjectURL(result.url)
        else created.asset = result
      })
      .catch(() => undefined)
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
