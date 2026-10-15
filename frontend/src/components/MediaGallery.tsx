import { useEffect, useState } from 'react'
import { DownloadIcon, FileIcon, PlayIcon } from './Icons'
import type { Media } from '../lib/types'

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Full-screen viewer for a single image or video. */
function Lightbox({ item, onClose }: { item: Media; onClose: () => void }) {
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
      {item.kind === 'video' ? (
        <video className="lightbox-media" src={item.url} controls autoPlay playsInline />
      ) : (
        <img className="lightbox-media" src={item.url} alt={item.alt_text} />
      )}
    </div>
  )
}

function Attachment({ item }: { item: Media }) {
  return (
    <a className="attachment" href={item.url} download onClick={(event) => event.stopPropagation()}>
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

/**
 * Render a post or message's attachments.
 *
 * One attachment gets a large frame; several get a grid. Videos are muted and
 * click-to-play so a feed full of them does not start a wall of sound.
 */
export function MediaGallery({ items }: { items: Media[] }) {
  const [open, setOpen] = useState<Media | null>(null)
  if (!items || items.length === 0) return null

  const visuals = items.filter((item) => item.kind !== 'application')
  const files = items.filter((item) => item.kind === 'application')

  return (
    <>
      {visuals.length > 0 && (
        <div className={`media-grid media-count-${Math.min(visuals.length, 4)}`}>
          {visuals.map((item) => (
            <button
              key={item.id}
              type="button"
              className="media-cell"
              onClick={(event) => {
                event.stopPropagation()
                setOpen(item)
              }}
              aria-label={item.alt_text || 'Open attachment'}
            >
              {item.kind === 'video' ? (
                <>
                  <video className="media-el" src={item.url} muted playsInline preload="metadata" />
                  <span className="media-play">
                    <PlayIcon size={30} />
                  </span>
                </>
              ) : (
                <img className="media-el" src={item.url} alt={item.alt_text} loading="lazy" />
              )}
            </button>
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
