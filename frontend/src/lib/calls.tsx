/**
 * Audio and video calls over the realtime socket.
 *
 * The server is a signalling relay only: it forwards SDP offers/answers and ICE
 * candidates between the two participants of a conversation and never touches
 * media. The call itself is a direct peer-to-peer WebRTC session, so no audio
 * or video passes through the API, and nothing about a call is stored.
 *
 * A call is always inside a conversation: ringing a stranger is not possible
 * because the relay only forwards to the other participant of a thread both
 * sides already belong to.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { ReactNode } from 'react'
import { useRealtime } from '../context/RealtimeContext'
import type { CallAction, RealtimeEvent } from '../context/RealtimeContext'
import * as queryCache from './queryCache'
import type { Conversation } from './types'

/** How long an unanswered outgoing call rings before it is abandoned. */
const RING_TIMEOUT_MS = 45_000

/** A call this long after the socket drops is over; the peers cannot recover. */
const DISCONNECT_GRACE_MS = 8_000

const RTC_CONFIG: RTCConfiguration = {
  // Public STUN only. A call between two peers behind symmetric NATs will fail
  // to connect without a TURN relay, and that is an accepted limitation here:
  // a TURN server costs money and this feature must not.
  iceServers: [{ urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] }],
}

export type CallMode = 'audio' | 'video'

/** Where a call is in its lifecycle, which drives what the overlay shows. */
export type CallPhase = 'idle' | 'calling' | 'ringing' | 'connecting' | 'active' | 'ended'

export interface CallState {
  phase: CallPhase
  mode: CallMode
  conversationId: number | null
  peerName: string
  peerColor: string
  peerAvatar: string | null
  /** Set on the peer's side of an incoming call, to render "wants to talk". */
  incoming: boolean
  /** Why the call ended, for the closing line. */
  endedReason: 'declined' | 'hangup' | 'timeout' | 'failed' | 'unavailable' | null
  micMuted: boolean
  cameraOff: boolean
  /** Whether the local stream has a camera track at all. */
  hasVideo: boolean
  durationMs: number
}

interface CallApi {
  state: CallState
  /** Media is ready and the offer has been sent. */
  start: (options: {
    conversationId: number
    mode: CallMode
    peerName: string
    peerColor: string
    peerAvatar: string | null
  }) => Promise<void>
  accept: () => Promise<void>
  decline: () => void
  hangup: () => void
  toggleMic: () => void
  toggleCamera: () => void
  /** The local preview and the peer's stream, for the overlay to render. */
  localStream: MediaStream | null
  remoteStream: MediaStream | null
}

const IDLE: CallState = {
  phase: 'idle',
  mode: 'audio',
  conversationId: null,
  peerName: '',
  peerColor: '#6366f1',
  peerAvatar: null,
  incoming: false,
  endedReason: null,
  micMuted: false,
  cameraOff: false,
  hasVideo: false,
  durationMs: 0,
}

const CallContext = createContext<CallApi | null>(null)

/** A short random id, so a stale frame from an earlier call is ignored. */
function newCallId(): string {
  return Math.random().toString(36).slice(2, 10)
}

export function CallProvider({ children }: { children: ReactNode }) {
  const { subscribe, sendCall, connected } = useRealtime()
  const [state, setState] = useState<CallState>(IDLE)
  const [localStream, setLocalStream] = useState<MediaStream | null>(null)
  const [remoteStream, setRemoteStream] = useState<MediaStream | null>(null)

  const pcRef = useRef<RTCPeerConnection | null>(null)
  const localRef = useRef<MediaStream | null>(null)
  const callIdRef = useRef<string | null>(null)
  // The conversation and mode of the live call, kept beside the call id.
  //
  // These cannot be read from `state`: `start` and the inbound `offer` branch
  // both set state and then build the peer connection in the same tick, so a
  // closure over `state` would still hold the *previous* conversation id (null
  // on a first call) and relay every ICE candidate to conversation 0.
  const conversationRef = useRef<number | null>(null)
  const modeRef = useRef<CallMode>('audio')
  // Set when this side offered, so an `answer` frame is only applied to the
  // peer connection that is waiting for one.
  const isCallerRef = useRef(false)
  // ICE candidates that arrive before the remote description is set. Applying
  // them early throws, and a candidate is never re-sent, so they are held.
  const pendingIceRef = useRef<RTCIceCandidateInit[]>([])
  const ringTimer = useRef<number | null>(null)
  const tickTimer = useRef<number | null>(null)
  const startedAt = useRef<number | null>(null)
  const disconnectTimer = useRef<number | null>(null)
  // The last frame the local side has seen, so a duplicate offer from a
  // reconnecting peer does not tear down a working call.
  const answeredRef = useRef(false)

  const clearTimers = useCallback(() => {
    if (ringTimer.current !== null) window.clearTimeout(ringTimer.current)
    if (tickTimer.current !== null) window.clearInterval(tickTimer.current)
    if (disconnectTimer.current !== null) window.clearTimeout(disconnectTimer.current)
    ringTimer.current = null
    tickTimer.current = null
    disconnectTimer.current = null
  }, [])

  const stopMedia = useCallback(() => {
    for (const track of localRef.current?.getTracks() ?? []) track.stop()
    localRef.current = null
    setLocalStream(null)
    setRemoteStream(null)
  }, [])

  const teardown = useCallback(() => {
    clearTimers()
    const pc = pcRef.current
    if (pc) {
      pc.onicecandidate = null
      pc.ontrack = null
      pc.onconnectionstatechange = null
      pc.close()
    }
    pcRef.current = null
    pendingIceRef.current = []
    answeredRef.current = false
    startedAt.current = null
    stopMedia()
  }, [clearTimers, stopMedia])

  /**
   * Finish a call and show its closing state for a moment.
   *
   * The overlay stays up rather than vanishing: a call that disappears without
   * a word leaves the user unsure whether it happened. A timer returns the
   * context to `idle`, which unmounts the overlay.
   */
  const end = useCallback(
    (reason: CallState['endedReason'], announce = false, action: CallAction = 'hangup') => {
      const callId = callIdRef.current
      const conversationId = conversationRef.current
      teardown()
      callIdRef.current = null
      conversationRef.current = null
      if (announce && callId && conversationId) {
        sendCall({ conversationId, action, callId, mode: modeRef.current })
      }
      if (reason === null) {
        setState(IDLE)
        return
      }
      setState((current) => ({ ...current, phase: 'ended', endedReason: reason }))
      window.setTimeout(() => {
        setState((current) => (current.phase === 'ended' ? IDLE : current))
      }, 2200)
    },
    [sendCall, teardown],
  )

  const buildPeer = useCallback(
    (mode: CallMode) => {
      const pc = new RTCPeerConnection(RTC_CONFIG)
      pcRef.current = pc

      const remote = new MediaStream()
      setRemoteStream(remote)
      pc.ontrack = (event) => {
        for (const track of event.streams[0]?.getTracks() ?? [event.track]) {
          remote.addTrack(track)
        }
        setRemoteStream(new MediaStream(remote.getTracks()))
        setState((current) => (current.phase === 'active' ? current : { ...current, phase: 'active' }))
      }

      pc.onicecandidate = (event) => {
        const callId = callIdRef.current
        const conversationId = conversationRef.current
        if (!event.candidate || !callId || !conversationId) return
        sendCall({
          conversationId,
          action: 'ice',
          callId,
          mode,
          payload: JSON.stringify(event.candidate.toJSON()),
        })
      }

      pc.onconnectionstatechange = () => {
        if (pc !== pcRef.current) return
        if (pc.connectionState === 'connected') {
          startedAt.current = Date.now()
          if (tickTimer.current !== null) window.clearInterval(tickTimer.current)
          tickTimer.current = window.setInterval(() => {
            setState((current) => ({
              ...current,
              durationMs: startedAt.current ? Date.now() - startedAt.current : 0,
            }))
          }, 1000)
          setState((current) => ({ ...current, phase: 'active' }))
        }
        if (pc.connectionState === 'failed') end('failed')
      }

      return pc
    },
    [end, sendCall],
  )

  const start = useCallback(
    async ({
      conversationId,
      mode,
      peerName,
      peerColor,
      peerAvatar,
    }: {
      conversationId: number
      mode: CallMode
      peerName: string
      peerColor: string
      peerAvatar: string | null
    }) => {
      if (state.phase !== 'idle' && state.phase !== 'ended') return
      teardown()
      const callId = newCallId()
      callIdRef.current = callId
      conversationRef.current = conversationId
      modeRef.current = mode
      isCallerRef.current = true

      setState({
        ...IDLE,
        phase: 'calling',
        mode,
        conversationId,
        peerName,
        peerColor,
        peerAvatar,
        hasVideo: mode === 'video',
      })

      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: mode === 'video' ? { facingMode: 'user' } : false,
        })
      } catch {
        // Permission denied, or no camera. The call is not placed at all: a
        // silent call is worse than a clear "we could not start".
        teardown()
        callIdRef.current = null
        setState({ ...IDLE, phase: 'ended', endedReason: 'unavailable' })
        window.setTimeout(() => setState(IDLE), 2600)
        return
      }

      localRef.current = stream
      setLocalStream(stream)

      const pc = buildPeer(mode)
      for (const track of stream.getTracks()) pc.addTrack(track, stream)

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      sendCall({ conversationId, action: 'offer', callId, mode, payload: offer.sdp })

      ringTimer.current = window.setTimeout(() => end('timeout', true), RING_TIMEOUT_MS)
    },
    [buildPeer, end, sendCall, state.phase, teardown],
  )

  const accept = useCallback(async () => {
    const conversationId = state.conversationId
    const callId = callIdRef.current
    const pc = pcRef.current
    if (!conversationId || !callId || !pc) return

    setState((current) => ({ ...current, phase: 'connecting', incoming: false }))

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: state.mode === 'video' ? { facingMode: 'user' } : false,
      })
    } catch {
      end('unavailable', true, 'decline')
      return
    }
    localRef.current = stream
    setLocalStream(stream)
    for (const track of stream.getTracks()) pc.addTrack(track, stream)

    // The offer was set when it arrived, so the answer can be produced now
    // that there is something to send.
    const answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    sendCall({
      conversationId,
      action: 'answer',
      callId,
      mode: state.mode,
      payload: answer.sdp,
    })
  }, [end, sendCall, state.conversationId, state.mode])

  const decline = useCallback(() => {
    end('declined', true, 'decline')
  }, [end])

  const hangup = useCallback(() => {
    end('hangup', true)
  }, [end])

  const toggleMic = useCallback(() => {
    const stream = localRef.current
    if (!stream) return
    const muted = !state.micMuted
    for (const track of stream.getAudioTracks()) track.enabled = !muted
    setState((current) => ({ ...current, micMuted: muted }))
  }, [state.micMuted])

  const toggleCamera = useCallback(() => {
    const stream = localRef.current
    if (!stream) return
    const off = !state.cameraOff
    for (const track of stream.getVideoTracks()) track.enabled = !off
    setState((current) => ({ ...current, cameraOff: off }))
  }, [state.cameraOff])

  /**
   * Handle one inbound call frame.
   *
   * Every branch checks the call id before touching the peer connection: a
   * frame from a call that has already ended, or from a previous one, must not
   * be able to reconfigure the current session.
   */
  const handleCallFrame = useCallback(
    async (event: RealtimeEvent) => {
      const action = event.action
      const conversationId = event.conversation_id
      const callId = event.call_id ?? null
      const mode: CallMode = event.mode === 'video' ? 'video' : 'audio'
      if (!action || !conversationId) return

      if (action === 'offer') {
        // A second offer while a call is up is a peer that reconnected and lost
        // its state. Anything else is a genuinely new call.
        const busy =
          state.phase !== 'idle' && state.phase !== 'ended' && state.phase !== 'calling'
        if (busy && callIdRef.current === callId) return
        teardown()
        callIdRef.current = callId
        conversationRef.current = conversationId
        modeRef.current = mode
        isCallerRef.current = false
        answeredRef.current = false
        // The frame carries ids only, so the caller's name comes from the
        // inbox the client already holds. A conversation that is not in the
        // cache yet (a very first message) still rings, just anonymously.
        const known = queryCache
          .read<Conversation[]>('conversations')
          ?.find((item) => item.id === conversationId)?.partner
        setState({
          ...IDLE,
          phase: 'ringing',
          mode,
          conversationId,
          peerName: known?.display_name ?? 'Someone',
          peerColor: known?.avatar_color ?? '#6366f1',
          peerAvatar: known?.avatar_url ?? null,
          incoming: true,
          hasVideo: mode === 'video',
        })

        const pc = buildPeer(mode)
        await pc.setRemoteDescription({ type: 'offer', sdp: event.payload ?? '' })
        for (const candidate of pendingIceRef.current) {
          try {
            await pc.addIceCandidate(candidate)
          } catch {
            // A candidate that no longer applies is not worth failing a call.
          }
        }
        pendingIceRef.current = []
        return
      }

      if (callId !== callIdRef.current) return

      if (action === 'answer') {
        const pc = pcRef.current
        if (!pc || !isCallerRef.current || answeredRef.current) return
        answeredRef.current = true
        await pc.setRemoteDescription({ type: 'answer', sdp: event.payload ?? '' })
        for (const candidate of pendingIceRef.current) {
          try {
            await pc.addIceCandidate(candidate)
          } catch {
            // As above.
          }
        }
        pendingIceRef.current = []
        if (ringTimer.current !== null) window.clearTimeout(ringTimer.current)
        ringTimer.current = null
        setState((current) => ({ ...current, phase: 'connecting' }))
        return
      }

      if (action === 'ice') {
        const candidate = event.payload ? (JSON.parse(event.payload) as RTCIceCandidateInit) : null
        if (!candidate) return
        const pc = pcRef.current
        if (!pc || !pc.remoteDescription) {
          pendingIceRef.current.push(candidate)
          return
        }
        try {
          await pc.addIceCandidate(candidate)
        } catch {
          // Ignore: an unusable candidate is not fatal, others may work.
        }
        return
      }

      if (action === 'accept') {
        // Informational: the answer is the frame that actually starts media.
        if (state.phase === 'calling') setState((current) => ({ ...current, phase: 'connecting' }))
        return
      }

      if (action === 'decline') {
        end('declined')
        return
      }

      if (action === 'hangup') {
        end('hangup')
      }
    },
    [buildPeer, end, state.phase, teardown],
  )

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type !== 'call') return
        void handleCallFrame(event)
      }),
    [subscribe, handleCallFrame],
  )

  // A dropped socket during a call ends it after a grace period. WebRTC media
  // can survive a signalling blip, so a brief drop is not fatal; a long one
  // means the peer cannot be reached for a hangup either.
  useEffect(() => {
    if (state.phase === 'idle' || state.phase === 'ended') return
    if (connected) {
      if (disconnectTimer.current !== null) window.clearTimeout(disconnectTimer.current)
      disconnectTimer.current = null
      return
    }
    if (disconnectTimer.current !== null) return
    disconnectTimer.current = window.setTimeout(() => end('failed'), DISCONNECT_GRACE_MS)
  }, [connected, end, state.phase])

  // Leaving the page ends the call rather than leaving the peer waiting.
  useEffect(() => {
    const onUnload = () => {
      const callId = callIdRef.current
      const conversationId = conversationRef.current
      if (callId && conversationId) {
        sendCall({ conversationId, action: 'hangup', callId, mode: modeRef.current })
      }
    }
    window.addEventListener('pagehide', onUnload)
    return () => window.removeEventListener('pagehide', onUnload)
  }, [sendCall])

  useEffect(() => teardown, [teardown])

  const value = useMemo<CallApi>(
    () => ({
      state,
      start,
      accept,
      decline,
      hangup,
      toggleMic,
      toggleCamera,
      localStream,
      remoteStream,
    }),
    [accept, decline, hangup, localStream, remoteStream, start, state, toggleCamera, toggleMic],
  )

  return <CallContext.Provider value={value}>{children}</CallContext.Provider>
}

export function useCall(): CallApi {
  const context = useContext(CallContext)
  if (!context) throw new Error('useCall must be used inside a CallProvider')
  return context
}
