import { useEffect, useRef, useState } from 'react'
import { CloseIcon } from './Icons'
import { Spinner } from './UI'
import { api } from '../lib/api'
import * as queryCache from '../lib/queryCache'
import type { Sticker } from '../lib/types'

const CACHE_KEY = 'stickers'

interface Props {
  open: boolean
  disabled?: boolean
  onPick: (sticker: Sticker) => void
  onClose: () => void
}

/**
 * The sticker tray above the message composer.
 *
 * The catalogue comes from the server, not from a constant here, so the ids
 * offered are exactly the ids the send route accepts — a stale bundle cannot
 * present a sticker the backend would reject.
 *
 * Keyboard behaviour matches a picker rather than a menu: Escape closes it, and
 * the panel is a labelled dialog so the grid is reachable and announced.
 */
export function StickerPicker({ open, disabled, onPick, onClose }: Props) {
  const [stickers, setStickers] = useState<Sticker[]>(() => queryCache.read<Sticker[]>(CACHE_KEY) ?? [])
  const [loading, setLoading] = useState(() => queryCache.read<Sticker[]>(CACHE_KEY) === undefined)
  const [error, setError] = useState<string | null>(null)
  const [pack, setPack] = useState<string>('Reactions')
  const panelRef = useRef<HTMLDivElement>(null)

  // The catalogue never changes within a deploy, so it is fetched once and
  // cached for the session: reopening the tray must not cost a round trip.
  useEffect(() => {
    if (!open) return
    if (queryCache.isFresh(CACHE_KEY, 60 * 60_000)) {
      const cached = queryCache.read<Sticker[]>(CACHE_KEY)
      if (cached) setStickers(cached)
      return
    }
    let active = true
    queryCache
      .load(CACHE_KEY, () => api.stickers(), 60 * 60_000)
      .then((data) => {
        if (active) {
          setStickers(data)
          setError(null)
        }
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Could not load stickers')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const packs = [...new Set(stickers.map((sticker) => sticker.pack))]
  const visible = stickers.filter((sticker) => sticker.pack === pack)

  return (
    <div className="sticker-tray" ref={panelRef} role="dialog" aria-label="Stickers">
      <div className="sticker-tray-head">
        <div className="sticker-packs" role="tablist" aria-label="Sticker packs">
          {packs.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={name === pack}
              className={`sticker-pack${name === pack ? ' active' : ''}`}
              onClick={() => setPack(name)}
            >
              {name}
            </button>
          ))}
        </div>
        <button type="button" className="icon-btn" aria-label="Close stickers" onClick={onClose}>
          <CloseIcon size={15} />
        </button>
      </div>

      {loading && (
        <div className="sticker-loading">
          <Spinner size={18} />
        </div>
      )}
      {!loading && error && <p className="sticker-error">{error}</p>}

      {!loading && !error && (
        <div className="sticker-grid">
          {visible.map((sticker) => (
            <button
              key={sticker.id}
              type="button"
              className="sticker-btn"
              title={sticker.label}
              aria-label={sticker.label}
              disabled={disabled}
              onClick={() => onPick(sticker)}
            >
              <span aria-hidden="true">{sticker.emoji}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
