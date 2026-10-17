/**
 * The notification chime, synthesised rather than loaded.
 *
 * A short two-note WebAudio blip avoids shipping an audio file, a network
 * round-trip and an autoplay-blocked `<audio>` element. The context is created
 * lazily and resumed on the first play, because browsers refuse to start audio
 * before a user gesture — which is why the first sound after a page load may
 * only be audible once the user has interacted.
 */

const SOUND_KEY = 'pulse.sound'

let context: AudioContext | null = null

function audioContext(): AudioContext | null {
  if (typeof window === 'undefined') return null
  const Ctor = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  if (context === null) context = new Ctor()
  return context
}

export function isSoundEnabled(): boolean {
  try {
    // Default on: the request is for a notification sound, so silence is the
    // opt-out rather than the starting state.
    return localStorage.getItem(SOUND_KEY) !== 'off'
  } catch {
    return true
  }
}

export function setSoundEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(SOUND_KEY, enabled ? 'on' : 'off')
  } catch {
    // Nothing to persist; the choice still holds for this page.
  }
  window.dispatchEvent(new CustomEvent('pulse:sound', { detail: enabled }))
}

/** Play the chime. Safe to call anywhere; a blocked context is a no-op. */
export function playNotificationSound(): void {
  if (!isSoundEnabled()) return
  const ctx = audioContext()
  if (ctx === null) return

  const start = () => {
    const now = ctx.currentTime
    // Two rising notes, the shape of a message alert without being shrill.
    const notes: Array<[number, number]> = [
      [880, 0],
      [1174.66, 0.09],
    ]
    for (const [frequency, offset] of notes) {
      const oscillator = ctx.createOscillator()
      const gain = ctx.createGain()
      oscillator.type = 'sine'
      oscillator.frequency.value = frequency
      // A short attack and exponential decay; a raw square edge clicks.
      gain.gain.setValueAtTime(0.0001, now + offset)
      gain.gain.exponentialRampToValueAtTime(0.09, now + offset + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.22)
      oscillator.connect(gain)
      gain.connect(ctx.destination)
      oscillator.start(now + offset)
      oscillator.stop(now + offset + 0.24)
    }
  }

  if (ctx.state === 'suspended') {
    ctx.resume().then(start).catch(() => undefined)
  } else {
    start()
  }
}
