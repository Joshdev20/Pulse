import { useEffect, useState } from 'react'
import { getToken } from './api'

/**
 * Load protected media into a blob URL.
 *
 * Media lives behind the same bearer auth as the rest of the API, and an
 * `<img src>` cannot send an Authorization header, so the bytes are fetched
 * with `fetch` and handed to the element as an object URL.
 *
 * Requests are cached per path and reference counted: several elements showing
 * the same attachment share one download, and the object URL is revoked once
 * the last of them unmounts.
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
  const response = await fetch(path, {
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

export function useMediaUrl(path: string | null | undefined): MediaAsset | null {
  const [asset, setAsset] = useState<MediaAsset | null>(null)

  useEffect(() => {
    if (!path) {
      setAsset(null)
      return
    }

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

    let cancelled = false
    entry.promise
      .then((result) => {
        if (!cancelled) setAsset(result)
      })
      .catch(() => {
        if (!cancelled) setAsset(null)
      })

    return () => {
      cancelled = true
      entry.refs -= 1
      if (entry.refs <= 0) {
        cache.delete(path)
        release(entry)
      }
    }
  }, [path])

  return asset
}
