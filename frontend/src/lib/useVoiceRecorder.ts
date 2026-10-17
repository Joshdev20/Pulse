import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Container/codec pairs tried in order when starting a recording.
 *
 * Support is uneven and not discoverable from `MediaRecorder` alone: Safari
 * records only MP4/AAC, Chrome prefers WebM/Opus, and Firefox's WebM support is
 * Opus-only. The list is ordered so each engine lands on something it can
 * actually encode, and the first entry a browser reports as supported is used.
 * An empty `mimeType` means "let the browser choose", which is the fallback for
 * an engine that reports none of these.
 */
const PREFERRED_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  'audio/ogg',
  '',
]

/** The file extension to store a recording under, per container. */
function extensionFor(mimeType: string): string {
  if (mimeType.startsWith('audio/mp4') || mimeType.includes('mp4')) return 'm4a'
  if (mimeType.startsWith('audio/ogg') || mimeType.includes('ogg')) return 'ogg'
  if (mimeType.startsWith('audio/wav')) return 'wav'
  return 'webm'
}

function pickMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  for (const candidate of PREFERRED_MIME_TYPES) {
    if (candidate === '') return ''
    if (MediaRecorder.isTypeSupported(candidate)) return candidate
  }
  return ''
}

export interface VoiceNote {
  blob: Blob
  /** Seconds recorded, measured from the recorder's own clock. */
  duration: number
  /** The container the recording is in, so it can be named and played back. */
  mimeType: string
}

export type RecorderStatus = 'idle' | 'requesting' | 'recording'

export interface RecorderState {
  status: RecorderStatus
  /** Seconds elapsed while recording, for the live counter. */
  elapsed: number
  error: string | null
  /** Whether this browser can record at all. */
  supported: boolean
  start: () => Promise<void>
  /** Stop and resolve with the finished recording, or null if nothing was captured. */
  stop: () => Promise<VoiceNote | null>
  cancel: () => void
}

/** Cap a single recording so a forgotten recording cannot fill the tab's memory. */
export const MAX_RECORDING_SECONDS = 120

/**
 * Record a voice note from the microphone.
 *
 * The stream is always released: every path that ends a recording stops its
 * tracks, so the browser's recording indicator goes out and the microphone is
 * not held open for the life of the page. A component that unmounts mid-recording
 * (navigating away from the thread) releases it too.
 */
export function useVoiceRecorder(): RecorderState {
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const startedAtRef = useRef(0)
  const tickRef = useRef<number | null>(null)
  // Resolved by `stop` once the recorder has flushed its final chunk. `onstop`
  // is the only reliable signal that the last chunk has arrived.
  const finishedRef = useRef<((note: VoiceNote | null) => void) | null>(null)
  const mimeRef = useRef('')
  // `stop` is called from a click handler and from the duration cap; a ref keeps
  // the second caller from starting a second stop on the same recorder.
  const stoppingRef = useRef(false)

  const supported =
    typeof navigator !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== 'undefined'

  const clearTick = useCallback(() => {
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current)
      tickRef.current = null
    }
  }, [])

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
  }, [])

  const reset = useCallback(() => {
    clearTick()
    releaseStream()
    recorderRef.current = null
    chunksRef.current = []
    stoppingRef.current = false
    setElapsed(0)
    setStatus('idle')
  }, [clearTick, releaseStream])

  const stop = useCallback(async (): Promise<VoiceNote | null> => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive' || stoppingRef.current) return null
    stoppingRef.current = true

    return new Promise<VoiceNote | null>((resolve) => {
      finishedRef.current = resolve
      // A recorder that never fires `onstop` (an engine that throws on stop)
      // must not leave the UI stuck in "recording".
      window.setTimeout(() => {
        if (finishedRef.current === resolve) {
          finishedRef.current = null
          reset()
          resolve(null)
        }
      }, 4000)
      recorder.stop()
    })
  }, [reset])

  const cancel = useCallback(() => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      // Detach first: a cancelled recording must not resolve a pending `stop`.
      finishedRef.current = null
      recorder.stop()
    }
    reset()
  }, [reset])

  const start = useCallback(async () => {
    if (status !== 'idle') return
    setError(null)

    if (!supported) {
      setError('This browser cannot record audio.')
      return
    }

    setStatus('requesting')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (caught) {
      setStatus('idle')
      // The two failures worth naming are a refused permission and the absence
      // of a microphone; anything else is reported generically.
      const name = caught instanceof DOMException ? caught.name : ''
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        setError('Microphone access was blocked. Allow it in your browser settings and try again.')
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        setError('No microphone was found on this device.')
      } else {
        setError('Could not start recording.')
      }
      return
    }

    streamRef.current = stream
    mimeRef.current = pickMimeType()

    let recorder: MediaRecorder
    try {
      recorder = mimeRef.current
        ? new MediaRecorder(stream, { mimeType: mimeRef.current })
        : new MediaRecorder(stream)
    } catch {
      // A constructor that rejects the negotiated type is recoverable: the
      // browser's own default is still usable.
      try {
        recorder = new MediaRecorder(stream)
        mimeRef.current = recorder.mimeType ?? ''
      } catch {
        setError('Could not start recording.')
        reset()
        return
      }
    }

    chunksRef.current = []
    recorderRef.current = recorder
    stoppingRef.current = false

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data)
    }

    recorder.onstop = () => {
      const duration = Math.max(0, (performance.now() - startedAtRef.current) / 1000)
      const chunks = chunksRef.current
      // Prefer the container the recorder actually produced over the one that
      // was requested; they can differ when the browser ignored the option.
      const type = recorder.mimeType || mimeRef.current || 'audio/webm'
      const blob = new Blob(chunks, { type })
      const resolve = finishedRef.current
      finishedRef.current = null
      reset()
      const note = blob.size > 0 ? { blob, duration, mimeType: type } : null
      if (resolve) resolve(note)
    }

    recorder.onerror = () => {
      setError('Recording failed.')
      finishedRef.current?.(null)
      finishedRef.current = null
      reset()
    }

    startedAtRef.current = performance.now()
    // A timeslice keeps chunks flowing, so a long recording is not one large
    // buffer and the first bytes exist before the user presses stop.
    recorder.start(250)
    setStatus('recording')

    tickRef.current = window.setInterval(() => {
      const seconds = (performance.now() - startedAtRef.current) / 1000
      setElapsed(seconds)
      if (seconds >= MAX_RECORDING_SECONDS) void stop()
    }, 200)
  }, [status, supported, reset, stop])

  // A component that unmounts mid-recording must not leave the microphone open.
  useEffect(
    () => () => {
      const recorder = recorderRef.current
      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null
        recorder.stop()
      }
      if (tickRef.current !== null) window.clearInterval(tickRef.current)
      streamRef.current?.getTracks().forEach((track) => track.stop())
    },
    [],
  )

  return { status, elapsed, error, supported, start, stop, cancel }
}

/** The extension to save a recording under, for the upload's filename. */
export { extensionFor as voiceNoteExtension }
