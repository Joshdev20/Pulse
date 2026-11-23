import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeftIcon, CloseIcon } from './Icons'
import { useMediaStatus } from '../lib/useMediaUrl'
import { VideoControls } from './VideoControls'
import { primeMedia } from '../lib/mediaCache'
import { useMediaReady } from '../lib/useMediaReady'
import { useAutoplay } from '../lib/preferences'
import { formatDuration } from '../lib/format'
import type { Media } from '../lib/types'

/** How far a finger must travel sideways before it counts as a page turn. */
const SWIPE_X = 55
/** How far down it must travel before it counts as "put it away". */
const SWIPE_DOWN = 110
/** Double-tap window, in milliseconds. */
const DOUBLE_TAP_MS = 280

/**
 * An upload with no description keeps its file name as the alt text, and a
 * phone's name — `434B4A7C-14AF-4490-85A2-E4FC90905B2A.png` — tells the reader
 * nothing, so it is not printed under the picture. A real description is.
 */
const FILE_NAME = /^[\w\-. ()]+\.(png|jpe?g|gif|webp|avif|heic|heif|bmp|tiff?|mp4|m4v|mov|webm|ogv|mkv|avi)$/i

function isBareFileName(text: string): boolean {
  return FILE_NAME.test(text)
}

/**
 * One attachment, full screen.
 *
 * The brief for this view is that opening, moving through and leaving a
 * picture should all cost one obvious gesture, so every exit is wired: the
 * close button, Escape, a tap on the empty space around it, a downward swipe,
 * and the phone's own back gesture (the viewer pushes a history entry, so back
 * closes it instead of leaving the post). Moving through a gallery is the same
 * on both inputs — arrows on a desktop, a sideways swipe on a phone.
 */
export function MediaViewer({
  items,
  index,
  onIndexChange,
  onClose,
}: {
  items: Media[]
  index: number
  onIndexChange: (next: number) => void
  onClose: () => void
}) {
  const item = items[index]
  const many = items.length > 1
  const autoplay = useAutoplay()
  // Where the media sits while a gesture is in progress. Kept apart from the
  // committed state so a cancelled drag snaps back with a transition.
  const [drag, setDrag] = useState({ x: 0, y: 0, active: false })
  const [zoom, setZoom] = useState(1)
  const touch = useRef<{ x: number; y: number; axis: 'none' | 'x' | 'y' } | null>(null)
  const lastTap = useRef(0)

  const step = useCallback(
    (delta: number) => {
      if (!many) return
      onIndexChange((index + delta + items.length) % items.length)
    },
    [index, items.length, many, onIndexChange],
  )

  // The parent hands down a fresh closure on every render (a page turn is one),
  // so the mount-time effects read it through a ref: re-running them would push
  // another history entry per turn and leave the reader pressing back a dozen
  // times to get out of the viewer.
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  const close = useCallback(() => {
    // If this viewer owns the top history entry, step back so the address bar
    // and the browser's back button agree with what is on screen.
    if ((window.history.state as { pulseViewer?: boolean } | null)?.pulseViewer) {
      window.history.back()
    }
    closeRef.current()
  }, [])

  useEffect(() => {
    // Already ours when the effect is re-run (StrictMode mounts twice), so the
    // entry is claimed once and back has one step to undo.
    if (!(window.history.state as { pulseViewer?: boolean } | null)?.pulseViewer) {
      window.history.pushState({ pulseViewer: true }, '')
    }
    const onPop = () => closeRef.current()
    window.addEventListener('popstate', onPop)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('popstate', onPop)
      document.body.style.overflow = previous
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close()
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        step(1)
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        step(-1)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [close, step])

  // A zoomed crop of the next picture would be disorienting, so turning the
  // page always starts at 1:1.
  useEffect(() => setZoom(1), [index])

  // The neighbours are almost certainly next, so fetch them while the reader
  // looks at this one. It is the same cache the grid uses, so nothing is
  // downloaded twice.
  useEffect(() => {
    for (const offset of [1, -1]) {
      const neighbour = items[(index + offset + items.length) % items.length]
      if (neighbour && items.length > 1) primeMedia(neighbour.poster_url ?? neighbour.url)
    }
  }, [index, items])

  const onTouchStart = (event: React.TouchEvent) => {
    if (event.touches.length !== 1) return
    const point = event.touches[0]
    touch.current = { x: point.clientX, y: point.clientY, axis: 'none' }

    const now = Date.now()
    if (now - lastTap.current < DOUBLE_TAP_MS) {
      lastTap.current = 0
      setZoom((value) => (value > 1 ? 1 : 2.2))
      return
    }
    lastTap.current = now
  }

  const onTouchMove = (event: React.TouchEvent) => {
    const start = touch.current
    if (!start || event.touches.length !== 1) return
    const point = event.touches[0]
    const dx = point.clientX - start.x
    const dy = point.clientY - start.y
    if (start.axis === 'none') {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return
      start.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'
    }
    // Zoomed in, a drag is for looking around, not for changing the page.
    if (zoom > 1) return
    if (start.axis === 'x' && many) {
      // Rubber-band at the ends so the gesture still feels bounded.
      const atEdge = (index === 0 && dx > 0) || (index === items.length - 1 && dx < 0)
      setDrag({ x: dx * (atEdge ? 0.3 : 0.6), y: 0, active: true })
    } else if (start.axis === 'y' && dy > 0) {
      setDrag({ x: 0, y: dy, active: true })
    }
  }

  const onTouchEnd = () => {
    const start = touch.current
    touch.current = null
    if (!start) return
    const { x, y } = drag
    setDrag({ x: 0, y: 0, active: false })
    if (zoom > 1) return
    if (start.axis === 'y' && y > SWIPE_DOWN) {
      close()
      return
    }
    if (start.axis === 'x' && Math.abs(x) > SWIPE_X) {
      step(x < 0 ? 1 : -1)
    }
  }

  const dismissProgress = Math.min(1, Math.max(0, drag.y) / (SWIPE_DOWN * 2))

  return (
    <div
      className="viewer"
      role="dialog"
      aria-modal="true"
      aria-label={item.alt_text || 'Attachment'}
      style={{ '--viewer-dismiss': dismissProgress } as React.CSSProperties}
      onClick={(event) => {
        if (event.target === event.currentTarget) close()
      }}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      <div className="viewer-bar" onClick={(event) => event.stopPropagation()}>
        <span className="viewer-count">
          {many ? `${index + 1} / ${items.length}` : item.kind === 'video' ? 'Video' : 'Photo'}
          {item.kind === 'video' && item.duration_ms != null && (
            <span className="viewer-count-dur">
              {formatDuration(item.duration_ms / 1000)}
            </span>
          )}
        </span>
        <div className="viewer-bar-actions">
          <button type="button" className="viewer-btn" onClick={close} aria-label="Close viewer">
            <CloseIcon size={20} />
          </button>
        </div>
      </div>

      <div
        className={`viewer-stage${drag.active ? ' dragging' : ''}${zoom > 1 ? ' zoomed' : ''}`}
        style={{ transform: `translate(${drag.x * 0.35}px, ${drag.y}px) scale(${1 - dismissProgress * 0.12})` }}
      >
        <ViewerStage
          key={item.id}
          item={item}
          zoom={zoom}
          autoplay={autoplay}
          onToggleZoom={() => setZoom((v) => (v > 1 ? 1 : 2.2))}
        />
      </div>

      {many && (
        <>
          <button
            type="button"
            className="viewer-nav prev"
            onClick={(event) => {
              event.stopPropagation()
              step(-1)
            }}
            aria-label="Previous attachment"
          >
            <ChevronLeftIcon size={22} />
          </button>
          <button
            type="button"
            className="viewer-nav next"
            onClick={(event) => {
              event.stopPropagation()
              step(1)
            }}
            aria-label="Next attachment"
          >
            <ChevronLeftIcon size={22} />
          </button>
        </>
      )}

      <div className="viewer-foot" onClick={(event) => event.stopPropagation()}>
        {item.alt_text && !isBareFileName(item.alt_text.trim()) && (
          <p className="viewer-caption">{item.alt_text}</p>
        )}
        <span className="viewer-hint">
          {many ? 'Swipe or use the arrows · Esc to close' : 'Esc or tap outside to close'}
        </span>
      </div>
    </div>
  )
}

/** The picture or clip itself, with the load state of its own download. */
function ViewerStage({
  item,
  zoom,
  onToggleZoom,
  autoplay,
}: {
  item: Media
  zoom: number
  onToggleZoom: () => void
  /** False when the reader turned autoplay off: the clip waits for a tap. */
  autoplay: boolean
}) {
  const { asset, failed } = useMediaStatus(item.poster_url ?? item.url)
  const { asset: full } = useMediaStatus(item.url)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  // A swipe that pages the gallery ends with a click on whatever it started
  // on, and that click must not also pause the clip.
  const dragged = useRef(false)
  const { ref: mediaRef, ready, markReady } = useMediaReady(item.kind === 'video' ? full?.url : asset?.url)
  const showSkeleton = !ready && !failed

  if (failed) {
    return <p className="viewer-error">This {item.kind} is no longer available.</p>
  }

  if (item.kind === 'video') {
    return (
      <div className="viewer-media-wrap" onClick={(event) => event.stopPropagation()}>
        {showSkeleton && <span className="viewer-skeleton skeleton" aria-hidden="true" />}
        <video
          ref={(node) => {
            videoRef.current = node
            mediaRef(node)
          }}
          className={`viewer-media${ready ? ' ready' : ''}`}
          src={full?.url}
          poster={asset?.url}
          autoPlay={autoplay}
          playsInline
          onTouchStart={() => {
            dragged.current = false
          }}
          onTouchMove={() => {
            dragged.current = true
          }}
          onClick={() => {
            if (dragged.current) return
            const video = videoRef.current
            if (!video) return
            if (video.paused) void video.play().catch(() => {})
            else video.pause()
          }}
          onLoadedMetadata={markReady}
          onLoadedData={markReady}
          onError={markReady}
        />
        <VideoControls videoRef={videoRef} />
      </div>
    )
  }

  return (
    <div className="viewer-media-wrap" onClick={(event) => event.stopPropagation()}>
      {showSkeleton && <span className="viewer-skeleton skeleton" aria-hidden="true" />}
      <img
        ref={mediaRef}
        className={`viewer-media${ready ? ' ready' : ''}`}
        style={{ transform: `scale(${zoom})`, cursor: zoom > 1 ? 'zoom-out' : 'zoom-in' }}
        src={asset?.url}
        alt={item.alt_text}
        onClick={onToggleZoom}
        onLoad={markReady}
        onError={markReady}
      />
    </div>
  )
}
