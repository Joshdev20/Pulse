import { useEffect, useRef, useState } from 'react'
import { PauseIcon, PlayIcon } from './Icons'
import { useMediaStatus } from '../lib/useMediaUrl'
import { formatDuration } from '../lib/format'
import type { Media } from '../lib/types'

/**
 * A voice note, with a play/pause control and a seekable progress bar.
 *
 * The element is driven from React state rather than the browser's default
 * controls, so the same look applies everywhere and the duration shown is the
 * clip's own rather than a placeholder. It is a real `<audio>` element, so
 * seeking, buffering and the platform's media session all keep working.
 */
export function VoiceNotePlayer({ item }: { item: Media }) {
  const { asset, failed } = useMediaStatus(item.url)
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)

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
  // Until metadata arrives the clip's own length is unknown; fall back to the
  // recording time the client sent with it, so the bar is not blank.
  const shown = duration || 0

  return (
    <div className={`voice-note${failed ? ' failed' : ''}`}>
      <button
        type="button"
        className="voice-play"
        onClick={toggle}
        disabled={failed || !asset}
        aria-label={playing ? 'Pause voice note' : 'Play voice note'}
      >
        {playing ? <PauseIcon size={17} /> : <PlayIcon size={17} />}
      </button>

      <div
        className="voice-track"
        onClick={seek}
        role="slider"
        aria-label="Seek voice note"
        aria-valuemin={0}
        aria-valuemax={Math.round(shown)}
        aria-valuenow={Math.round(current)}
        tabIndex={0}
      >
        <span className="voice-fill" style={{ width: `${progress * 100}%` }} />
      </div>

      <span className="voice-time">
        {failed ? 'unavailable' : formatDuration(playing || current > 0 ? current : shown)}
      </span>

      {asset && !failed && (
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
            if (Number.isFinite(value)) setDuration(value)
          }}
        />
      )}
    </div>
  )
}
