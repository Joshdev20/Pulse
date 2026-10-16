import { useEffect, useState } from 'react'

/**
 * Poster frames for uploaded videos, captured in the browser.
 *
 * The backend has no ffmpeg (and the free instance has no room for one), so a
 * thumbnail cannot be produced at upload time. It does not need to be: the
 * video's bytes are already downloaded to render the tile, and a `<video>`
 * element can be asked to decode one frame from them. That frame is drawn to a
 * canvas and kept as a small image URL, so a feed of videos shows real previews
 * instead of a black box behind a play button.
 *
 * Entries are keyed by the media path, so every element showing the same
 * attachment shares one capture. This is a module-level cache for the same
 * reason `mediaCache` is: the work is per-attachment, not per-component.
 */

/** Longest edge of a generated poster. Small: it is only a feed thumbnail. */
const POSTER_MAX_EDGE = 640

/** Give up if the frame has not decoded in this long. */
const CAPTURE_TIMEOUT_MS = 8000

type PosterEntry = {
  promise: Promise<string | null>
  /** Consumers currently interested; the entry is dropped at zero. */
  refs: number
}

const posters = new Map<string, PosterEntry>()

function captureFrame(video: HTMLVideoElement): string | null {
  const width = video.videoWidth
  const height = video.videoHeight
  if (!width || !height) return null

  const scale = Math.min(1, POSTER_MAX_EDGE / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width * scale))
  canvas.height = Math.max(1, Math.round(height * scale))
  const context = canvas.getContext('2d')
  if (!context) return null

  try {
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', 0.72)
  } catch {
    // A cross-origin source taints the canvas and `toDataURL` throws. Media is
    // same-origin here, so this is a guard rather than an expected path.
    return null
  }
}

/**
 * Decode one frame of `src` and return it as a data URL, or null on failure.
 *
 * Resolves to null rather than rejecting: a poster is an enhancement, and a
 * video that cannot be previewed must still render as a playable tile.
 */
function generate(src: string): Promise<string | null> {
  return new Promise((resolve) => {
    const video = document.createElement('video')
    video.preload = 'auto'
    video.muted = true
    video.playsInline = true
    // The blob URL is same-origin, but set this so a future absolute media URL
    // cannot taint the canvas without a CORS grant.
    video.crossOrigin = 'anonymous'
    video.src = src

    let settled = false
    const finish = (result: string | null) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      video.removeAttribute('src')
      video.load()
      resolve(result)
    }

    const timer = window.setTimeout(() => finish(null), CAPTURE_TIMEOUT_MS)

    // Seek a little way in: frame zero of many uploads is a black fade-in.
    video.addEventListener('loadedmetadata', () => {
      const target = Number.isFinite(video.duration) ? Math.min(0.5, video.duration / 2) : 0.1
      try {
        video.currentTime = Math.max(0.1, target)
      } catch {
        finish(captureFrame(video))
      }
    })

    // `seeked` is the reliable "a frame is ready to draw" signal. `loadeddata`
    // is a fallback for an engine that will not seek.
    video.addEventListener('seeked', () => finish(captureFrame(video)))
    video.addEventListener('loadeddata', () => {
      if (video.readyState >= 2) finish(captureFrame(video))
    })
    video.addEventListener('error', () => finish(null))
  })
}

/** The shared poster entry for `path`, starting a capture if needed. */
function ref(path: string): PosterEntry {
  let entry = posters.get(path)
  if (!entry) {
    const created: PosterEntry = { promise: generate(path), refs: 0 }
    created.promise
      .then((url) => {
        // Nothing wanted it in the end; do not keep a dead entry around.
        if (url === null || created.refs <= 0) {
          if (posters.get(path) === created) posters.delete(path)
        }
      })
      .catch(() => {
        if (posters.get(path) === created) posters.delete(path)
      })
    posters.set(path, created)
    entry = created
  }
  entry.refs += 1
  return entry
}

function unref(path: string, entry: PosterEntry) {
  entry.refs -= 1
  if (entry.refs > 0) return
  // Deferred, like the media cache: StrictMode remounts in one tick and would
  // otherwise cancel a capture the remount immediately wants back.
  queueMicrotask(() => {
    if (entry.refs <= 0 && posters.get(path) === entry) posters.delete(path)
  })
}

/**
 * A poster data URL for the video at `path`, or null while it is being made.
 *
 * Returns the resolved value only: the caller shows its normal loading state
 * until a frame is ready, then swaps in the picture.
 */
export function useVideoPoster(path: string | null | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!path) {
      setUrl(null)
      return
    }
    let cancelled = false
    setUrl(null)
    const entry = ref(path)
    entry.promise
      .then((result) => {
        if (!cancelled) setUrl(result)
      })
      .catch(() => {
        if (!cancelled) setUrl(null)
      })
    return () => {
      cancelled = true
      unref(path, entry)
    }
  }, [path])

  return url
}

/** Drop every generated poster. Used when the account changes. */
export function clearVideoPosters() {
  posters.clear()
}
