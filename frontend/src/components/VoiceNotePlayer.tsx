import { useEffect, useMemo, useRef, useState } from 'react'
import { PauseIcon, PlayIcon } from './Icons'
import { useMediaStatus } from '../lib/useMediaUrl'
import { formatDuration } from '../lib/format'
import type { Media } from '../lib/types'

/** Bars drawn across the track. Enough to read as a waveform at any width. */
const BAR_COUNT = 34

/**
 * A stable bar height for a given clip, so the same note always draws the same
 * shape.
 *
 * These are decoration, not a measurement: decoding the real peaks would mean
 * downloading the whole clip before it could be drawn, which is the cost the
 * player exists to avoid. Deriving them from the media id keeps the shape
 * consistent across renders and readers while still differing per note.
 */
function barHeights(seed: number): number[] {
  const heights: number[] = []
  let state = seed * 2654435761
  for (let index = 0; index < BAR_COUNT; index += 1) {
    state = (state * 1664525 + 1013904223) % 4294967296
    const unit = state / 4294967296
    // A gentle arc keeps the middle taller than the edges, which reads as a
    // voice rather than as noise.
    const arc = Math.sin((index / (BAR_COUNT - 1)) * Math.PI)
    heights.push(0.3 + unit * 0.45 + arc * 0.25)
  }
  return heights
}

/**
 * A voice note, drawn as a play control over a seekable waveform.
 *
 * The length comes from the clip's own `duration_ms` first, so it is on screen
 * the moment the bubble renders; the audio element's metadata only refines it.
 * A note uploaded before durations were stored still shows its length once the
 * element loads, which is the one case that needs a fetch.
 */
export function VoiceNotePlayer({ item }: { item: Media }) {
  const [retryCount, setRetryCount] = useState(0)
  const { asset, failed } = useMediaStatus(item.url, retryCount)
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [loaded, setLoaded] = useState(0)
  // A clip the element could not decode — a truncated upload, or a container
  // this browser has no codec for — is as unplayable as one that never arrived.
  // Without this the button stayed enabled, `play()` rejected silently, and a
  // tap on it did nothing at all.
  //
  // It is keyed on the source rather than a plain flag so a retry gets a fresh
  // attempt: the re-fetch produces a new blob URL, and that one must not inherit
  // the previous failure.
  const [undecodableSrc, setUndecodableSrc] = useState<string | null>(null)

  // The stored length in seconds, or null when the upload predates it.
  const stored = item.duration_ms ? item.duration_ms / 1000 : 0
  const duration = loaded || stored
  const heights = useMemo(() => barHeights(Math.abs(item.id)), [item.id])
  const undecodable = Boolean(asset && undecodableSrc === asset.url)
  const unplayable = failed || undecodable

  /**
   * Fetch the note again, once.
   *
   * "Unavailable" used to be final: the bytes were cached, the element had
   * already refused them, and nothing re-ran the download. But the cause is
   * often not a broken clip — it is a note stored in a format this device
   * cannot decode, which the server converts when it stores or repairs it. A
   * retry is what lets that conversion be picked up without reloading the app.
   */
  function retry() {
    if (retryCount > 0) return
    setUndecodableSrc(null)
    setLoaded(0)
    setCurrent(0)
    setRetryCount(1)
  }

  // A single element is shared by every voice note in a thread only in the sense
  // that each player owns one; starting a new clip leaves the previous one
  // playing unless it is paused, which reads as two notes at once.
  useEffect(() => {
    if (!playing) return
    const audio = audioRef.current
    if (!audio) return
    const pauseOthers = () => {
      document.querySelectorAll('audio').forEach((other) => {
        if (other !== audio) other.pause()
      })
    }
    pauseOthers()
  }, [playing])

  function toggle() {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      void audio.play().catch(() => undefined)
    } else {
      audio.pause()
    }
  }

  function seek(event: React.MouseEvent<HTMLDivElement>) {
    const audio = audioRef.current
    if (!audio || !duration) return
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1)
    audio.currentTime = ratio * duration
    setCurrent(audio.currentTime)
  }

  const progress = duration > 0 ? Math.min(current / duration, 1) : 0
  // While playing, the elapsed time replaces the length, the way a player shows
  // where it is rather than what it is.
  const shown = playing || current > 0 ? current : duration
  const playedBars = Math.round(progress * BAR_COUNT)

  return (
    <div className={`voice-note${unplayable ? ' failed' : ''}${playing ? ' playing' : ''}`}>
      <button
        type="button"
        className="voice-play"
        onClick={toggle}
        disabled={unplayable || !asset}
        aria-label={playing ? 'Pause voice note' : 'Play voice note'}
      >
        {playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
      </button>

      <div
        className="voice-track"
        onClick={seek}
        role="slider"
        aria-label="Seek voice note"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(current)}
        tabIndex={0}
      >
        <span className="voice-wave" aria-hidden="true">
          {heights.map((height, index) => (
            <span
              key={index}
              className={`voice-bar${index < playedBars ? ' played' : ''}`}
              style={{ height: `${Math.round(height * 100)}%` }}
            />
          ))}
        </span>
      </div>

      <span className="voice-time">
        {unplayable ? (
          <button
            type="button"
            className="voice-retry"
            onClick={retry}
            disabled={retryCount > 0}
            title="Try playing this voice note again"
          >
            {retryCount > 0 ? 'unavailable' : 'tap to retry'}
          </button>
        ) : duration > 0 ? (
          formatDuration(shown)
        ) : (
          '--:--'
        )}
      </span>

      {asset && !unplayable && (
        <audio
          ref={audioRef}
          src={asset.url}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            setPlaying(false)
            setCurrent(0)
          }}
          onTimeUpdate={(event) => setCurrent(event.currentTarget.currentTime)}
          onLoadedMetadata={(event) => {
            const value = event.currentTarget.duration
            if (Number.isFinite(value)) setLoaded(value)
          }}
          onError={() => {
            setPlaying(false)
            setUndecodableSrc(asset.url)
          }}
        />
      )}
    </div>
  )
}
