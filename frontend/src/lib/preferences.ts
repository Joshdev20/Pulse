import { useSyncExternalStore } from 'react'

/**
 * Playback preferences, kept on this device.
 *
 * These describe how this screen should behave rather than anything about the
 * account, so they live in `localStorage` and never reach the server. A reader
 * on a metered connection turns autoplay off once and every tab remembers.
 */

const AUTOPLAY_KEY = 'pulse.autoplay'

const listeners = new Set<() => void>()

function readAutoplay(): boolean {
  try {
    // Default on: a feed of silent clips that starts itself is the behaviour
    // people expect, so silence is the opt-out rather than the starting state.
    return localStorage.getItem(AUTOPLAY_KEY) !== 'off'
  } catch {
    return true
  }
}

let autoplay = typeof window === 'undefined' ? true : readAutoplay()

function emit(): void {
  for (const listener of listeners) listener()
}

export function isAutoplayEnabled(): boolean {
  return autoplay
}

export function setAutoplayEnabled(enabled: boolean): void {
  autoplay = enabled
  try {
    localStorage.setItem(AUTOPLAY_KEY, enabled ? 'on' : 'off')
  } catch {
    // Nothing to persist; the choice still holds for this page.
  }
  emit()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Follow the choice, including a change made in another tab. */
export function useAutoplay(): boolean {
  return useSyncExternalStore(subscribe, isAutoplayEnabled, () => true)
}

/**
 * Which inline clip, if any, is allowed to make noise.
 *
 * Autoplay is silent by default and every browser refuses to start an
 * unmuted clip without a gesture, so sound is granted one clip at a time by
 * the reader tapping its speaker. A single owner also means two tiles can
 * never talk over each other.
 */
let soundOwner: number | null = null
const soundListeners = new Set<() => void>()

export function setInlineSoundOwner(mediaId: number | null): void {
  if (soundOwner === mediaId) return
  soundOwner = mediaId
  for (const listener of soundListeners) listener()
}

export function inlineSoundOwner(): number | null {
  return soundOwner
}

export function useInlineSound(mediaId: number): boolean {
  return useSyncExternalStore(
    (listener) => {
      soundListeners.add(listener)
      return () => {
        soundListeners.delete(listener)
      }
    },
    () => soundOwner === mediaId,
    () => false,
  )
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== AUTOPLAY_KEY) return
    autoplay = readAutoplay()
    emit()
  })
}
