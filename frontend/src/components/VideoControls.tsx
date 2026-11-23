import { useCallback, useEffect, useState } from 'react'
import type { RefObject } from 'react'
import {
  ExpandIcon,
  PauseIcon,
  PlayIcon,
  ShrinkIcon,
  VolumeIcon,
  VolumeOffIcon,
} from './Icons'
import { formatDuration } from '../lib/format'

/**
 * The player's own controls.
 *
 * The browser's built-in ones keep a download entry in their overflow menu,
 * and that menu lives in a closed shadow tree, so there is no attribute to
 * remove it with. Drawing the bar here is the only way to leave it out: play,
 * seek, sound, full screen, and nothing else.
 */
export function VideoControls({
  videoRef,
}: {
  videoRef: RefObject<HTMLVideoElement | null>
}) {
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [full, setFull] = useState(false)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const sync = () => {
      setPlaying(!video.paused && !video.ended)
      setMuted(video.muted || video.volume === 0)
      setTime(video.currentTime || 0)
      if (Number.isFinite(video.duration)) setDuration(video.duration)
    }
    const events = [
      'play',
      'pause',
      'ended',
      'volumechange',
      'timeupdate',
      'durationchange',
      'loadedmetadata',
      'seeking',
    ]
    for (const name of events) video.addEventListener(name, sync)
    sync()
    return () => {
      for (const name of events) video.removeEventListener(name, sync)
    }
  }, [videoRef])

  useEffect(() => {
    const onChange = () => setFull(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const togglePlay = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    if (video.paused) void video.play().catch(() => {})
    else video.pause()
  }, [videoRef])

  const toggleMute = useCallback(() => {
    const video = videoRef.current
    if (video) video.muted = !video.muted
  }, [videoRef])

  const toggleFull = useCallback(() => {
    const video = videoRef.current
    if (document.fullscreenElement) {
      void document.exitFullscreen()
      return
    }
    const wrap = video?.closest('.viewer-media-wrap') as HTMLElement | null
    if (wrap?.requestFullscreen) {
      void wrap.requestFullscreen().catch(() => {})
      return
    }
    // iOS only offers full screen on the element itself.
    const ios = video as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null
    ios?.webkitEnterFullscreen?.()
  }, [videoRef])

  // Space and K are the two shortcuts a player cannot leave out.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== ' ' && event.key !== 'k' && event.key !== 'K') return
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'BUTTON', 'A', 'TEXTAREA', 'SELECT'].includes(target.tagName)) {
        return
      }
      event.preventDefault()
      togglePlay()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [togglePlay])

  const seek = (value: number) => {
    const video = videoRef.current
    if (!video) return
    video.currentTime = value
    setTime(value)
  }

  // The viewer reads swipes and double taps on its root, so a drag on the
  // seek bar must not reach it.
  const swallow = (event: React.SyntheticEvent) => event.stopPropagation()

  return (
    <div
      className="vctl"
      onClick={swallow}
      onTouchStart={swallow}
      onTouchMove={swallow}
      onTouchEnd={swallow}
    >
      <input
        className="vctl-seek"
        type="range"
        min={0}
        max={duration || 0}
        step={0.05}
        value={Math.min(time, duration || 0)}
        onChange={(event) => seek(Number(event.target.value))}
        aria-label="Seek"
      />
      <div className="vctl-row">
        <button
          type="button"
          className="vctl-btn"
          onClick={togglePlay}
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
        </button>
        <span className="vctl-time">
          {formatDuration(time)} <span className="vctl-sep">/</span> {formatDuration(duration)}
        </span>
        <span className="vctl-gap" />
        <button
          type="button"
          className="vctl-btn"
          onClick={toggleMute}
          aria-label={muted ? 'Unmute' : 'Mute'}
        >
          {muted ? <VolumeOffIcon size={18} /> : <VolumeIcon size={18} />}
        </button>
        <button
          type="button"
          className="vctl-btn"
          onClick={toggleFull}
          aria-label="Full screen"
        >
          {full ? <ShrinkIcon size={18} /> : <ExpandIcon size={18} />}
        </button>
      </div>
    </div>
  )
}
