import { useRef, useState } from 'react'
import { CloseIcon, ImageIcon } from './Icons'
import { Spinner } from './UI'
import { api, ApiError } from '../lib/api'
import { useMediaUrl } from '../lib/useMediaUrl'
import type { Media } from '../lib/types'

const MAX_ATTACHMENTS = 4

/** Uploaded-but-not-yet-posted preview. Owns its own authenticated fetch. */
function Thumb({ item, onRemove }: { item: Media; onRemove: () => void }) {
  const asset = useMediaUrl(item.url)
  return (
    <div className="media-thumb">
      {item.kind === 'image' ? (
        <img src={asset?.url} alt={item.alt_text} />
      ) : (
        <span className="media-thumb-file">{item.content_type.split('/')[1]}</span>
      )}
      <button type="button" className="media-thumb-remove" onClick={onRemove} aria-label="Remove attachment">
        <CloseIcon size={12} />
      </button>
    </div>
  )
}

/**
 * File picker that uploads immediately and hands back media ids.
 *
 * Uploading on selection rather than on submit means the slow part happens
 * while the user is still typing, and the post request itself stays small.
 */
export function MediaPicker({
  items,
  onChange,
  disabled,
  compact = false,
  showThumbsOnly = false,
}: {
  items: Media[]
  onChange: (items: Media[]) => void
  disabled?: boolean
  compact?: boolean
  /** Render only the thumbnail strip; the trigger button lives elsewhere. */
  showThumbsOnly?: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(0)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const full = items.length >= MAX_ATTACHMENTS

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    setError(null)

    const room = MAX_ATTACHMENTS - items.length
    const chosen = Array.from(files).slice(0, room)
    if (chosen.length < files.length) {
      setError(`Up to ${MAX_ATTACHMENTS} attachments per post`)
    }

    const uploaded: Media[] = []
    for (const file of chosen) {
      setUploading((count) => count + 1)
      setProgress(0)
      try {
        uploaded.push(await api.upload(file, file.name, setProgress))
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : 'Upload failed')
      } finally {
        setUploading((count) => count - 1)
      }
    }
    if (uploaded.length > 0) onChange([...items, ...uploaded])
    if (inputRef.current) inputRef.current.value = ''
  }

  function remove(id: number) {
    onChange(items.filter((item) => item.id !== id))
  }

  return (
    <div className="media-picker">
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp,video/mp4,video/webm,application/pdf"
        multiple
        hidden
        onChange={(event) => void handleFiles(event.target.files)}
      />

      <button
        type="button"
        className={compact ? 'icon-btn' : 'btn btn-ghost btn-sm'}
        disabled={disabled || full || uploading > 0}
        onClick={() => inputRef.current?.click()}
        title={full ? `Up to ${MAX_ATTACHMENTS} attachments` : 'Add photo, video or file'}
        aria-label="Add attachment"
        hidden={showThumbsOnly}
      >
        {uploading > 0 ? <Spinner size={17} /> : <ImageIcon size={18} />}
        {!compact && <span>Media</span>}
      </button>

      {uploading > 0 && <span className="media-progress">{progress}%</span>}

      {items.length > 0 && (
        <div className="media-thumbs">
          {items.map((item) => (
            <Thumb key={item.id} item={item} onRemove={() => remove(item.id)} />
          ))}
        </div>
      )}

      {error && <p className="field-error">{error}</p>}
    </div>
  )
}
