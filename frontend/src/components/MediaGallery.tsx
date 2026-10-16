import { useEffect, useState } from 'react'
import { DownloadIcon, FileIcon, PlayIcon } from './Icons'
import { useMediaStatus, useMediaUrl } from '../lib/useMediaUrl'
import { primeMedia } from '../lib/mediaCache'
import { useMediaReady } from '../lib/useMediaReady'
import { useVideoPoster } from '../lib/videoPoster'
import { formatDuration } from '../lib/format'
import type { Media } from '../lib/types'

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Full-screen viewer for a single image or video. */
function Lightbox({ item, onClose }: { item: Media; onClose: () => void }) {
  const { asset, failed } = useMediaStatus(item.url)
  const { ref: mediaRef, ready, markReady } = useMediaReady(asset?.url)
  const showSkeleton = !ready && !failed

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  return (
    <div
      className="lightbox"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-label={item.alt_text || 'Attachment'}
    >
      <button type="button" className="lightbox-close" onClick={onClose} aria-label="Close">
        ✕
      </button>
      {showSkeleton && <span className="lightbox-skeleton skeleton" aria-hidden="true" />}
      {failed && <p className="lightbox-error">This {item.kind} is no longer available.</p>}
      {item.kind === 'video' ? (
        <video
          ref={mediaRef}
          className={`lightbox-media${ready ? ' ready' : ''}`}
          src={asset?.url}
          controls
          autoPlay
          playsInline
          /* See MediaCell: `loadedmetadata` fires under `preload="metadata"` on
             every engine, `loadeddata` does not. */
          onLoadedMetadata={markReady}
          onLoadedData={markReady}
          onError={markReady}
        />
      ) : (
        <img
          ref={mediaRef}
          className={`lightbox-media${ready ? ' ready' : ''}`}
          src={asset?.url}
          alt={item.alt_text}
          onLoad={markReady}
          onError={markReady}
        />
      )}
    </div>
  )
}

function Attachment({ item }: { item: Media }) {
  const asset = useMediaUrl(item.url)
  return (
    <a
      className="attachment"
      href={asset?.url}
      download={asset?.filename ?? undefined}
      onClick={(event) => event.stopPropagation()}
    >
      <span className="attachment-icon">
        <FileIcon size={18} />
      </span>
      <span className="attachment-info">
        <span className="attachment-name">{item.alt_text || `Attachment ${item.id}`}</span>
        <span className="attachment-meta">
          {item.content_type} · {humanSize(item.size_bytes)}
        </span>
      </span>
      <span className="attachment-download">
        <DownloadIcon size={16} />
      </span>
    </a>
  )
}

/** One tile in the grid. Owns its own fetch so hooks stay per-item. */
function MediaCell({ item, onOpen }: { item: Media; onOpen: () => void }) {
  const { asset, failed } = useMediaStatus(item.url)
  const { ref: mediaRef, ready, markReady } = useMediaReady(asset?.url)
  const poster = useVideoPoster(item.kind === 'video' ? asset?.url : null)
  const [duration, setDuration] = useState<number | null>(null)
  const showSkeleton = !ready && !failed

  // A pointer resting on a tile is a strong hint the click is coming, so start
  // the download now and the lightbox opens onto bytes already in memory.
  const warm = () => primeMedia(item.url)

  return (
    <button
      type="button"
      className="media-cell"
      onClick={(event) => {
        event.stopPropagation()
        onOpen()
      }}
      onPointerEnter={warm}
      onFocus={warm}
      aria-label={item.alt_text || 'Open attachment'}
    >
      {showSkeleton && <span className="media-skeleton skeleton" aria-hidden="true" />}
      {item.kind === 'video' ? (
        <>
          <video
            ref={mediaRef}
            className={`media-el${ready ? ' ready' : ''}${failed ? ' failed' : ''}`}
            src={asset?.url}
            muted
            playsInline
            preload="metadata"
            /* `loadeddata` alone is not reliable under `preload="metadata"`:
               Safari (notably on iOS) fetches the header only, reports
               `readyState` 1 and never fires `loadeddata`, leaving the tile
               stuck behind `opacity: 0`. `loadedmetadata` fires everywhere and
               is enough to reveal the first frame behind the play badge. */
            onLoadedMetadata={(event) => {
              markReady()
              const value = event.currentTarget.duration
              if (Number.isFinite(value)) setDuration(value)
            }}
            onLoadedData={markReady}
            onError={markReady}
          />
          {/* The captured frame covers the video's own first frame, so the
              tile shows a real preview even where the element paints black. */}
          {poster && !failed && <img className="media-poster" src={poster} alt="" aria-hidden="true" />}
          {failed ? (
            <span className="media-error">Video unavailable</span>
          ) : (
            <>
              <span className="media-play">
                <PlayIcon size={30} />
              </span>
              {duration !== null && <span className="media-duration">{formatDuration(duration)}</span>}
            </>
          )}
        </>
      ) : (
        <>
          <img
            ref={mediaRef}
            className={`media-el${ready ? ' ready' : ''}`}
            src={asset?.url}
            alt={item.alt_text}
            loading="lazy"
            decoding="async"
            onLoad={markReady}
            onError={markReady}
          />
          {/* A 404 here is normal on this deployment: uploads made before the
              bytes were stored in the database were lost with the instance's
              disk, so say so rather than leaving an empty frame. */}
          {failed && <span className="media-error">Image unavailable</span>}
        </>
      )}
    </button>
  )
}

/**
 * Render a post or message's attachments.
 *
 * One attachment gets a large frame; several get a grid. Videos are muted and
 * click-to-play so a feed full of them does not start a wall of sound.
 */
export function MediaGallery({ items }: { items: Media[] }) {
  const [open, setOpen] = useState<Media | null>(null)

  // Kick off every attachment's download as soon as the gallery mounts. Each
  // cell would otherwise start its own fetch only once React runs its effects,
  // which serializes work that the browser is happy to do in parallel.
  useEffect(() => {
    for (const item of items ?? []) primeMedia(item.url)
  }, [items])

  if (!items || items.length === 0) return null

  const visuals = items.filter((item) => item.kind !== 'application')
  const files = items.filter((item) => item.kind === 'application')

  return (
    <>
      {visuals.length > 0 && (
        <div className={`media-grid media-count-${Math.min(visuals.length, 4)}`}>
          {visuals.map((item) => (
            <MediaCell key={item.id} item={item} onOpen={() => setOpen(item)} />
          ))}
        </div>
      )}

      {files.map((item) => (
        <Attachment key={item.id} item={item} />
      ))}

      {open && <Lightbox item={open} onClose={() => setOpen(null)} />}
    </>
  )
}
