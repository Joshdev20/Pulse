import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar } from '../components/Avatar'
import { MediaGallery } from '../components/MediaGallery'
import { MediaPicker } from '../components/MediaPicker'
import { EmptyState, ErrorState, FeedSkeleton, Spinner } from '../components/UI'
import { ChevronLeftIcon, MailIcon, PaperclipIcon, SendIcon } from '../components/Icons'
import { useRealtime } from '../context/RealtimeContext'
import { api, ApiError } from '../lib/api'
import { timeAgo } from '../lib/format'
import type { Conversation, Media, Message } from '../lib/types'

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
              size="md"
            />
            <div className="inbox-info">
              <div className="inbox-top">
                <span className="inbox-name">{conversation.partner.display_name}</span>
                <span className="inbox-time">{timeAgo(conversation.last_message_at)}</span>
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

  const scrollRef = useRef<HTMLDivElement>(null)
  const typingTimer = useRef<number | null>(null)
  const lastTypingSent = useRef(0)

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

  // Live messages and typing indicators for this thread only.
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
        if (event.type === 'typing' && event.conversation_id === conversation.id) {
          setPartnerTyping(true)
          if (typingTimer.current !== null) window.clearTimeout(typingTimer.current)
          typingTimer.current = window.setTimeout(() => setPartnerTyping(false), 3500)
        }
      }),
    [subscribe, conversation.id],
  )

  useEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, partnerTyping])

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
      )
      setMessages((current) =>
        current.some((item) => item.id === sent.id) ? current : [...current, sent],
      )
      setDraft('')
      setAttachments([])
      onSent()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Message not sent')
    } finally {
      setSending(false)
    }
  }

  const canSend = (draft.trim().length > 0 || attachments.length > 0) && !sending

  return (
    <div className="thread">
      <header className="thread-head">
        <Link to={`/u/${conversation.partner.username}`} className="thread-partner">
          <Avatar
            name={conversation.partner.display_name}
            color={conversation.partner.avatar_color}
            size="md"
          />
          <div>
            <div className="thread-name">{conversation.partner.display_name}</div>
            <div className="thread-handle">@{conversation.partner.username}</div>
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
          <div key={message.id} className={`bubble-row${message.mine ? ' mine' : ''}`}>
            {!message.mine && (
              <Avatar
                name={message.sender.display_name}
                color={message.sender.avatar_color}
                size="sm"
              />
            )}
            <div className={`bubble${message.mine ? ' mine' : ''}`}>
              {message.body && <p>{message.body}</p>}
              <MediaGallery items={message.media} />
              <time className="bubble-time" dateTime={message.created_at}>
                {timeAgo(message.created_at)}
              </time>
            </div>
          </div>
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

      <form className="composer-inline thread-composer" onSubmit={submit}>
        <MediaPicker items={attachments} onChange={setAttachments} disabled={sending} compact />
        <textarea
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
