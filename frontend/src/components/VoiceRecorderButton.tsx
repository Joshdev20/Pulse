import { useRef } from 'react'
import { CloseIcon, MicIcon, StopIcon } from './Icons'
import { Spinner } from './UI'
import { useVoiceRecorder, voiceNoteExtension, type VoiceNote } from '../lib/useVoiceRecorder'
import { formatDuration } from '../lib/format'

/**
 * Record button for a message composer.
 *
 * While idle this is a microphone; while recording it becomes a stop button
 * beside a live timer, and a cancel control discards the take. The finished
 * clip is handed to `onRecorded`, which uploads it and sends it as a message —
 * the same path an attached file takes, so a voice note is stored and delivered
 * exactly like any other attachment.
 */
export function VoiceRecorderButton({
  disabled,
  onRecorded,
  onError,
}: {
  disabled?: boolean
  onRecorded: (note: VoiceNote) => Promise<void> | void
  onError: (message: string) => void
}) {
  const recorder = useVoiceRecorder()
  const busyRef = useRef(false)

  async function stopAndSend() {
    if (busyRef.current) return
    busyRef.current = true
    try {
      const note = await recorder.stop()
      if (note) {
        await onRecorded(note)
      } else if (!recorder.error) {
        onError('Nothing was recorded.')
      }
    } finally {
      busyRef.current = false
    }
  }

  if (recorder.status === 'recording') {
    return (
      <div className="voice-recording" role="status" aria-live="polite">
        <span className="voice-dot" aria-hidden="true" />
        <span className="voice-elapsed">{formatDuration(recorder.elapsed)}</span>
        <button
          type="button"
          className="icon-btn voice-stop"
          onClick={() => void stopAndSend()}
          aria-label="Stop recording and send"
          title="Stop and send"
        >
          <StopIcon size={16} />
        </button>
        <button
          type="button"
          className="icon-btn danger"
          onClick={recorder.cancel}
          aria-label="Cancel recording"
          title="Cancel"
        >
          <CloseIcon size={16} />
        </button>
      </div>
    )
  }

  return (
    <>
      <button
        type="button"
        className="icon-btn voice-trigger"
        disabled={disabled || recorder.status === 'requesting' || !recorder.supported}
        onClick={() => void recorder.start()}
        title={
          recorder.supported
            ? 'Record a voice note'
            : 'This browser cannot record audio'
        }
        aria-label="Record a voice note"
      >
        {recorder.status === 'requesting' ? <Spinner size={17} /> : <MicIcon size={18} />}
      </button>
      {recorder.error && <p className="field-error voice-error">{recorder.error}</p>}
    </>
  )
}

/** The filename to upload a recording under, derived from its container. */
export function voiceNoteFilename(note: VoiceNote): string {
  return `voice-note.${voiceNoteExtension(note.mimeType)}`
}
