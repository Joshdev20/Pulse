import { useEffect, useRef } from 'react'
import { Avatar } from './Avatar'
import {
  CameraFlipIcon,
  MicIcon,
  MicOffIcon,
  PhoneIcon,
  VideoIcon,
  VideoOffIcon,
} from './Icons'
import { useCall } from '../lib/calls'
import { formatDuration } from '../lib/format'

/** The closing line for each way a call can end. */
const ENDED_TEXT: Record<string, string> = {
  declined: 'Call declined',
  hangup: 'Call ended',
  timeout: 'No answer',
  failed: 'Connection lost',
  unavailable: 'Could not start the call',
}

function statusLine(phase: string, endedReason: string | null, durationMs: number): string {
  if (phase === 'ended') return ENDED_TEXT[endedReason ?? 'hangup'] ?? 'Call ended'
  if (phase === 'calling') return 'Ringing…'
  if (phase === 'ringing') return 'Incoming call'
  if (phase === 'connecting') return 'Connecting…'
  if (phase === 'active') return formatDuration(durationMs / 1000)
  return ''
}

/**
 * The full-screen call surface.
 *
 * One overlay for both roles: an incoming call shows accept/decline, an active
 * one shows the media and the hang-up controls. Video is drawn as a large
 * remote picture with a small self-preview; an audio call has no video elements
 * at all, only the avatar, so nothing has to be hidden or re-laid-out.
 */
export function CallOverlay() {
  const {
    state,
    accept,
    decline,
    hangup,
    toggleMic,
    toggleCamera,
    localStream,
    remoteStream,
  } = useCall()

  const localRef = useRef<HTMLVideoElement>(null)
  const remoteRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (localRef.current && localRef.current.srcObject !== localStream) {
      localRef.current.srcObject = localStream
    }
  }, [localStream])

  useEffect(() => {
    if (remoteRef.current && remoteRef.current.srcObject !== remoteStream) {
      remoteRef.current.srcObject = remoteStream
    }
  }, [remoteStream])

  if (state.phase === 'idle') return null

  const ringing = state.phase === 'ringing'
  const ended = state.phase === 'ended'
  const showVideo = state.mode === 'video' && state.phase === 'active' && !state.cameraOff

  return (
    <div className={`call-overlay${ended ? ' ended' : ''}`} role="dialog" aria-modal="true" aria-label="Call">
      {showVideo && (
        <video
          ref={remoteRef}
          className="call-remote"
          autoPlay
          playsInline
          aria-label={`${state.peerName} video`}
        />
      )}
      {state.mode === 'video' && !showVideo && <div className="call-video-fallback" aria-hidden="true" />}

      <div className="call-info">
        {(!showVideo || ringing) && (
          <div className={`call-avatar${ringing ? ' ringing' : ''}`}>
            <Avatar
              name={state.peerName}
              color={state.peerColor}
              url={state.peerAvatar}
              size="xl"
              eager
            />
          </div>
        )}
        <h2 className="call-name">{state.peerName}</h2>
        <p className="call-status" aria-live="polite">
          {statusLine(state.phase, state.endedReason, state.durationMs)}
        </p>
        {state.mode === 'video' && ringing && (
          <span className="call-kind">
            <VideoIcon size={14} /> Video call
          </span>
        )}
      </div>

      {state.mode === 'video' && state.phase === 'active' && (
        <video
          ref={localRef}
          className={`call-local${state.cameraOff ? ' hidden' : ''}`}
          autoPlay
          playsInline
          muted
          aria-label="Your camera"
        />
      )}
      {/* Audio-only calls still need the local element mounted so the track is
          attached; it is never shown. */}
      {state.mode === 'audio' && <video ref={localRef} className="call-audio-sink" autoPlay playsInline muted />}

      <div className="call-controls">
        {ringing ? (
          <>
            <button type="button" className="call-btn decline" onClick={decline} aria-label="Decline call">
              <PhoneIcon size={22} />
            </button>
            <button type="button" className="call-btn accept" onClick={() => void accept()} aria-label="Accept call">
              <PhoneIcon size={22} />
            </button>
          </>
        ) : (
          <>
            {!ended && (
              <button
                type="button"
                className={`call-btn soft${state.micMuted ? ' off' : ''}`}
                onClick={toggleMic}
                aria-pressed={state.micMuted}
                aria-label={state.micMuted ? 'Unmute microphone' : 'Mute microphone'}
              >
                {state.micMuted ? <MicOffIcon size={20} /> : <MicIcon size={20} />}
              </button>
            )}
            {!ended && state.hasVideo && (
              <button
                type="button"
                className={`call-btn soft${state.cameraOff ? ' off' : ''}`}
                onClick={toggleCamera}
                aria-pressed={state.cameraOff}
                aria-label={state.cameraOff ? 'Turn camera on' : 'Turn camera off'}
              >
                {state.cameraOff ? <VideoOffIcon size={20} /> : <VideoIcon size={20} />}
              </button>
            )}
            {!ended && (
              <button
                type="button"
                className="call-btn decline"
                onClick={hangup}
                aria-label="End call"
              >
                <PhoneIcon size={22} />
              </button>
            )}
            {ended && (
              <button type="button" className="call-btn soft" onClick={hangup} aria-label="Close">
                <CameraFlipIcon size={20} />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
