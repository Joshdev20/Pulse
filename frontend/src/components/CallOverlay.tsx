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
 * on screen at all, only the avatar, so nothing has to be hidden or re-laid-out.
 *
 * The remote element is mounted for every call, audio included: an audio track
 * only plays once it is attached to a media element, so an audio call with no
 * remote sink is a call with no sound.
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
  const remoteVideoRef = useRef<HTMLVideoElement>(null)
  const remoteAudioRef = useRef<HTMLAudioElement>(null)

  // The remote element that exists for this mode owns the stream. Attaching to
  // both would play the audio twice on a video call.
  const remoteRef = state.mode === 'video' ? remoteVideoRef : remoteAudioRef
  // Nothing is attached until the call has been accepted. `ontrack` fires when
  // the offer is applied, which is *before* the callee has agreed to anything,
  // and an element holding the peer's stream at that point is a live sink for
  // their microphone the moment ICE completes.
  const accepting = state.phase === 'connecting' || state.phase === 'active'

  useEffect(() => {
    const node = remoteRef.current
    if (!node) return
    const wanted = accepting ? remoteStream : null
    if (node.srcObject !== wanted) node.srcObject = wanted
  }, [remoteRef, remoteStream, accepting, state.mode])

  useEffect(() => {
    if (localRef.current && localRef.current.srcObject !== localStream) {
      localRef.current.srcObject = localStream
    }
  }, [localStream])

  if (state.phase === 'idle') return null

  const ringing = state.phase === 'ringing'
  const ended = state.phase === 'ended'
  // Video is only shown once media is actually flowing. Before that the
  // overlay shows the avatar, so an un-accepted call is never a black screen.
  // `cameraOff` is the *local* camera and must not hide the peer's picture.
  const showRemoteVideo = state.mode === 'video' && state.phase === 'active'

  return (
    <div className={`call-overlay${ended ? ' ended' : ''}`} role="dialog" aria-modal="true" aria-label="Call">
      {state.mode === 'video' && (
        <video
          ref={remoteVideoRef}
          className={`call-remote${showRemoteVideo ? '' : ' hidden'}`}
          autoPlay
          playsInline
          aria-label={`${state.peerName} video`}
        />
      )}
      {state.mode === 'audio' && (
        // Never seen, but it is what actually plays the other person's voice.
        <audio ref={remoteAudioRef} autoPlay aria-label={`${state.peerName} audio`} />
      )}

      <div className={`call-info${showRemoteVideo ? ' over-video' : ''}`}>
        {!showRemoteVideo && (
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
        {state.mode === 'video' && !showRemoteVideo && !ended && (
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
      {/* Audio-only calls still need a local element mounted so the track is
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
