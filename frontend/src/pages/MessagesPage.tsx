import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar, DisplayName } from '../components/Avatar'
import { MediaPicker } from '../components/MediaPicker'
import { VoiceRecorderButton, voiceNoteFilename } from '../components/VoiceRecorderButton'
import { MessageBubble } from '../components/MessageBubble'
import { EmptyState, ErrorState, FeedSkeleton, RelativeTime, Spinner } from '../components/UI'
import { ChevronLeftIcon, CloseIcon, MailIcon, PaperclipIcon, ReplyIcon, SendIcon } from '../components/Icons'
import { useRealtime } from '../context/RealtimeContext'
import { api, ApiError } from '../lib/api'
import { formatPresence } from '../lib/format'
import { useNow } from '../lib/useNow'
import type { VoiceNote } from '../lib/useVoiceRecorder'
import type { Conversation, Media, Message } from '../lib/types'

/** How long after opening a thread to keep it pinned to the newest message. */
const PIN_WINDOW_MS = 2500

/** A `scrollHeight` change smaller than this is not worth re-pinning for. */
const GROWTH_TOLERANCE_PX = 2

/** Inbox: every conversation, newest activity first. */
function Inbox({
  conversations,
  activeId,
  loading,
  error,
  onReload,
}: {
  conversations: Conversation[]
  activeId?: number
  loading: boolean
  error: string | null
  onReload: () => void
}) {
  if (loading) return <FeedSkeleton rows={4} />
  if (error) return <ErrorState message={error} onRetry={onReload} />
  if (conversations.length === 0) {
    return (
      <EmptyState
        icon={<MailIcon size={24} />}
        title="No messages yet"
        description="Open someone's profile and choose Message to start a conversation."
      />
    )
  }

  return (
    <ul className="inbox">
      {conversations.map((conversation) => (
        <li key={conversation.id}>
          <Link
            to={`/messages/${conversation.id}`}
            className={`inbox-row${conversation.id === activeId ? ' active' : ''}`}
          >
            <Avatar
              name={conversation.partner.display_name}
              color={conversation.partner.avatar_color}
              url={conversation.partner.avatar_url}
              size="md"
            />
            <div className="inbox-info">
              <div className="inbox-top">
                <span className="inbox-name">
                  <DisplayName user={conversation.partner} />
                </span>
                <RelativeTime iso={conversation.last_message_at} className="inbox-time" />
              </div>
              <div className="inbox-preview">
                {conversation.last_message
                  ? conversation.last_message.mine
                    ? `You: ${conversation.last_message.body || 'Attachment'}`
                    : conversation.last_message.body || 'Attachment'
                  : 'Say hello'}
              </div>
            </div>
            {conversation.unread_count > 0 && (
              <span className="inbox-unread">{conversation.unread_count}</span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  )
}

/** A single thread with live delivery, typing state and attachments. */
function Thread({ conversation, onSent }: { conversation: Conversation; onSent: () => void }) {

  const { subscribe, sendTyping } = useRealtime()
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<Media[]>([])
  const [sending, setSending] = useState(false)
  const [partnerTyping, setPartnerTyping] = useState(false)
  const [replyTo, setReplyTo] = useState<Message | null>(null)
  // Live presence for the partner, seeded from the conversation payload and
  // then updated by `presence` frames without refetching the inbox.
  const [partner, setPartner] = useState(conversation.partner)

  useEffect(() => {
    setPartner(conversation.partner)
  }, [conversation.partner])

  const scrollRef = useRef<HTMLDivElement>(null)
  const typingTimer = useRef<number | null>(null)
  const lastTypingSent = useRef(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // Presence is shown to the minute, so the header only needs to re-render
  // about that often rather than on every tick.
  const now = useNow(30_000)

  const load = useCallback(() => {
    setLoading(true)
    api
      .messages(conversation.id, 50)
      .then(setMessages)
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : 'Could not load messages'),
      )
      .finally(() => setLoading(false))
  }, [conversation.id])

  useEffect(() => {
    load()
    void api.markConversationRead(conversation.id).catch(() => undefined)
    onSent()
  }, [conversation.id, load, onSent])

  // Live messages, likes and typing indicators for this thread only.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message' && event.message?.conversation_id === conversation.id) {
          setMessages((current) =>
            current.some((item) => item.id === event.message!.id)
              ? current
              : [...current, event.message!],
          )
          void api.markConversationRead(conversation.id).catch(() => undefined)
        }
        if (event.type === 'message_like' && event.conversation_id === conversation.id) {
          setMessages((current) =>
            current.map((item) =>
              item.id === event.message_id
                ? { ...item, like_count: event.like_count ?? item.like_count }
                : item,
            ),
          )
        }
        if (event.type === 'presence' && event.user_id === conversation.partner.id) {
          setPartner((current) => ({
            ...current,
            is_online: event.is_online ?? false,
            last_seen_at: event.last_seen_at ?? current.last_seen_at,
          }))
        }
        if (event.type === 'typing' && event.conversation_id === conversation.id) {
          setPartnerTyping(true)
          if (typingTimer.current !== null) window.clearTimeout(typingTimer.current)
          typingTimer.current = window.setTimeout(() => setPartnerTyping(false), 3500)
        }
      }),
    [subscribe, conversation.id],
  )

  useLayoutEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, partnerTyping, replyTo, conversation.id])

  // Whether the newest message should still be kept in view, and the last
  // content height seen. Both outlive the re-subscription below, so a reader
  // who has scrolled up is not yanked back down by the next attachment.
  const pinned = useRef(true)
  const contentHeight = useRef(0)

  // Only opening a thread re-arms the pin; scrolling up disarms it for good, so
  // arriving messages never fight a reader who is reading history.
  useEffect(() => {
    pinned.current = true
  }, [conversation.id])

  // The window restarts on every content change, so it is measured from when
  // the thread last settled rather than from the click. A thread that opens on
  // a cold start, or one still receiving messages, therefore stays pinned; a
  // quiet thread stops after this long so late-decoding old images cannot keep
  // moving the view.
  useEffect(() => {
    if (!pinned.current) return
    const timer = window.setTimeout(() => {
      pinned.current = false
    }, PIN_WINDOW_MS)
    return () => window.clearTimeout(timer)
  }, [conversation.id, messages, partnerTyping, replyTo])

  /**
   * Keep the newest message in view while the thread's content is still settling.
   *
   * The scroll above runs before the attachments have decoded, and an image
   * holds only a 120px placeholder until then — a tall photo grows the thread
   * by hundreds of pixels afterwards, leaving the view stuck that far above the
   * bottom, which reads as the thread having opened at the top. Re-pinning on
   * growth is what closes that gap.
   *
   * Re-subscribing on `messages` is what makes the children observable at all:
   * they render only once the history has loaded, which is after this effect
   * first runs for a conversation.
   */
  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    contentHeight.current = node.scrollHeight

    const stop = () => {
      pinned.current = false
    }
    node.addEventListener('wheel', stop, { passive: true })
    node.addEventListener('touchstart', stop, { passive: true })
    node.addEventListener('keydown', stop)

    const observer = new ResizeObserver(() => {
      if (!pinned.current) return
      const height = node.scrollHeight
      if (height - contentHeight.current > GROWTH_TOLERANCE_PX) node.scrollTop = height
      contentHeight.current = height
    })
    // The observer has to follow the content, not the scroll box: the box is a
    // fixed-height flex child, so its own border box never resizes.
    for (const child of Array.from(node.children)) observer.observe(child)

    return () => {
      observer.disconnect()
      node.removeEventListener('wheel', stop)
      node.removeEventListener('touchstart', stop)
      node.removeEventListener('keydown', stop)
    }
  }, [conversation.id, messages, partnerTyping, replyTo])

  function startReply(message: Message) {
    setReplyTo(message)
    // Focus the composer so the swipe lands the user where they expect.
    inputRef.current?.focus()
  }

  /**
   * Flip the like on a message, optimistically.
   *
   * The server is the authority on the total, so its answer is applied on
   * success; a failure rolls the bubble back to what it showed before.
   */
  async function toggleLike(message: Message) {
    const next = !message.liked_by_me
    const optimistic = {
      ...message,
      liked_by_me: next,
      like_count: Math.max(0, message.like_count + (next ? 1 : -1)),
    }
    setMessages((current) => current.map((item) => (item.id === message.id ? optimistic : item)))
    try {
      const result = next
        ? await api.likeMessage(conversation.id, message.id)
        : await api.unlikeMessage(conversation.id, message.id)
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? { ...item, liked_by_me: result.liked_by_me, like_count: result.like_count }
            : item,
        ),
      )
    } catch {
      setMessages((current) => current.map((item) => (item.id === message.id ? message : item)))
    }
  }

  function onDraftChange(value: string) {
    setDraft(value)
    // Throttle the indicator: at most one every two seconds while typing.
    const now = Date.now()
    if (value && now - lastTypingSent.current > 2000) {
      lastTypingSent.current = now
      sendTyping(conversation.id)
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const body = draft.trim()
    if ((!body && attachments.length === 0) || sending) return

    setSending(true)
    setError(null)
    try {
      const sent = await api.sendMessage(
        conversation.id,
        body,
        attachments.map((item) => item.id),
        replyTo?.id ?? null,
      )
      setMessages((current) =>
        current.some((item) => item.id === sent.id) ? current : [...current, sent],
      )
      setDraft('')
      setAttachments([])
      setReplyTo(null)
      onSent()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Message not sent')
    } finally {
      setSending(false)
    }
  }

  const canSend = (draft.trim().length > 0 || attachments.length > 0) && !sending

  /**
   * Upload a finished recording and send it as the next message.
   *
   * The clip is uploaded first and then referenced by id, which is the same
   * path an attached file takes, so a voice note needs no separate storage or
   * delivery route.
   */
  async function sendVoiceNote(note: VoiceNote) {
    if (sending) return
    setSending(true)
    setError(null)
    try {
      // A `File` rather than the raw blob: the upload path names the part from
      // it, and the server sniffs the bytes, so the name only has to carry the
      // right container.
      const file = new File([note.blob], voiceNoteFilename(note), { type: note.mimeType })
      const media = await api.upload(file, 'Voice note')
      const sent = await api.sendMessage(
        conversation.id,
        '',
        [media.id],
        replyTo?.id ?? null,
      )
      setMessages((current) =>
        current.some((item) => item.id === sent.id) ? current : [...current, sent],
      )
      setReplyTo(null)
      onSent()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Voice note not sent')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="thread">
      <header className="thread-head">
        <Link to={`/u/${partner.username}`} className="thread-partner">
          <span className="thread-avatar">
            <Avatar
              name={partner.display_name}
              color={partner.avatar_color}
              url={partner.avatar_url}
              size="md"
            />
            {partner.is_online && <span className="presence-dot" aria-hidden="true" />}
          </span>
          <div>
            <div className="thread-name">
              <DisplayName user={partner} />
            </div>
            <div className={`thread-handle${partner.is_online ? ' online' : ''}`}>
              {formatPresence(partner.last_seen_at, partner.is_online, now)}
            </div>
          </div>
        </Link>
      </header>

      <div className="thread-body" ref={scrollRef}>
        {loading && <FeedSkeleton rows={3} />}
        {!loading && error && messages.length === 0 && (
          <ErrorState message={error} onRetry={load} />
        )}
        {!loading && messages.length === 0 && (
          <p className="thread-empty">
            This is the beginning of your conversation with {conversation.partner.display_name}.
          </p>
        )}

        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            onReply={startReply}
            onToggleLike={toggleLike}
          />
        ))}

        {partnerTyping && (
          <div className="bubble-row">
            <div className="bubble typing-bubble" aria-live="polite">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        )}
      </div>

      {error && messages.length > 0 && <p className="thread-error">{error}</p>}

      {replyTo && (
        <div className="reply-preview">
          <ReplyIcon size={15} />
          <div className="reply-preview-body">
            <span className="reply-preview-name">{replyTo.sender.display_name}</span>
            <span className="reply-preview-text">
              {replyTo.body || (replyTo.media.length > 0 ? 'Attachment' : '')}
            </span>
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="Cancel reply"
            onClick={() => setReplyTo(null)}
          >
            <CloseIcon size={15} />
          </button>
        </div>
      )}

      <form className="composer-inline thread-composer" onSubmit={submit}>
        <MediaPicker items={attachments} onChange={setAttachments} disabled={sending} />
        <VoiceRecorderButton
          disabled={sending}
          onRecorded={sendVoiceNote}
          onError={setError}
        />
        <textarea
          ref={inputRef}
          className="input thread-input"
          placeholder={`Message @${conversation.partner.username}`}
          value={draft}
          rows={1}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void submit(event)
            }
          }}
          aria-label="Message"
        />
        <button
          type="submit"
          className="btn btn-primary btn-icon"
          disabled={!canSend}
          aria-label="Send message"
        >
          {sending ? <Spinner size={16} /> : <SendIcon size={18} />}
        </button>
      </form>
    </div>
  )
}

export function MessagesPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()

  const { subscribe } = useRealtime()

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const activeId = conversationId ? Number(conversationId) : undefined

  const load = useCallback(() => {
    setLoading(true)
    api
      .conversations()
      .then(setConversations)
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : 'Could not load conversations'),
      )
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Any incoming message reorders the inbox.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message') load()
      }),
    [subscribe, load],
  )

  const active = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId),
    [conversations, activeId],
  )

  // On a narrow screen the inbox and the thread take turns, so the URL drives
  // which one is visible.
  const showThread = Boolean(activeId)

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          {showThread && (
            <button
              type="button"
              className="icon-btn thread-back"
              onClick={() => navigate('/messages')}
              aria-label="Back to inbox"
            >
              <ChevronLeftIcon size={20} />
            </button>
          )}
          <div style={{ flex: 1 }}>
            <h1>{showThread ? active?.partner.display_name ?? 'Messages' : 'Messages'}</h1>
            <div className="sub">
              {showThread ? `@${active?.partner.username ?? ''}` : 'Private conversations'}
            </div>
          </div>
        </div>
      </header>

      <div className={`messages${showThread ? ' show-thread' : ''}`}>
        <div className="messages-inbox">
          <Inbox
            conversations={conversations}
            activeId={activeId}
            loading={loading}
            error={error}
            onReload={load}
          />
        </div>
        <div className="messages-thread">
          {active ? (
            <Thread conversation={active} onSent={load} />
          ) : (
            activeId && (
              <div className="state">
                <Spinner size={20} />
              </div>
            )
          )}
          {!activeId && (
            <EmptyState
              icon={<PaperclipIcon size={24} />}
              title="Select a conversation"
              description="Pick a thread on the left, or start a new one from someone's profile."
            />
          )}
        </div>
      </div>
    </>
  )
}
