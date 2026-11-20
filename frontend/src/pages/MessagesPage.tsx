import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar, DisplayName } from '../components/Avatar'
import { MediaPicker } from '../components/MediaPicker'
import { VoiceRecorderButton, voiceNoteFilename } from '../components/VoiceRecorderButton'
import { MessageBubble } from '../components/MessageBubble'
import { MessageActionSheet } from '../components/MessageActionSheet'
import type { MessageAction } from '../components/MessageActionSheet'
import { StickerPicker } from '../components/StickerPicker'
import { Modal, EmptyState, ErrorState, FeedSkeleton, RelativeTime, Spinner } from '../components/UI'
import {
  ChevronLeftIcon,
  CloseIcon,
  MailIcon,
  PaperclipIcon,
  PhoneIcon,
  ReplyIcon,
  SearchIcon,
  SendIcon,
  StickerIcon,
  VideoIcon,
} from '../components/Icons'
import { useRealtime } from '../context/RealtimeContext'
import { useCall } from '../lib/calls'
import { api, ApiError } from '../lib/api'
import * as queryCache from '../lib/queryCache'
import { decrement } from '../lib/unread'
import { formatPresence } from '../lib/format'
import { previewOf } from '../lib/stickers'
import { useNow } from '../lib/useNow'
import type { VoiceNote } from '../lib/useVoiceRecorder'
import type { Conversation, Media, Message, Sticker } from '../lib/types'

/** Cache key for the inbox list. */
const INBOX_KEY = 'conversations'

/** How long after opening a thread to keep it pinned to the newest message. */
const PIN_WINDOW_MS = 2500

/** A `scrollHeight` change smaller than this is not worth re-pinning for. */
const GROWTH_TOLERANCE_PX = 2

/** A message this close to the previous one is drawn in the same group. */
const GROUP_WINDOW_MS = 4 * 60_000

/** Two messages more than this far apart get a date divider between them. */
const DAY_MS = 24 * 60 * 60_000

function dayKey(iso: string): string {
  const date = new Date(iso)
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
}

function dayLabel(iso: string, now: number): string {
  const date = new Date(iso)
  const today = new Date(now)
  const yesterday = new Date(now - DAY_MS)
  if (dayKey(iso) === dayKey(today.toISOString())) return 'Today'
  if (dayKey(iso) === dayKey(yesterday.toISOString())) return 'Yesterday'
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric',
  })
}

/** Inbox: every conversation, newest activity first, searchable. */
function Inbox({
  conversations,
  activeId,
  loading,
  error,
  onReload,
  typingIn,
}: {
  conversations: Conversation[]
  activeId?: number
  loading: boolean
  error: string | null
  onReload: () => void
  /** Conversations where the partner is typing right now. */
  typingIn: Set<number>
}) {
  const [query, setQuery] = useState('')

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return conversations
    return conversations.filter((conversation) => {
      const partner = conversation.partner
      return (
        partner.display_name.toLowerCase().includes(needle) ||
        partner.username.toLowerCase().includes(needle)
      )
    })
  }, [conversations, query])

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
    <>
      <div className="inbox-search">
        <SearchIcon size={15} />
        <input
          className="inbox-search-input"
          type="search"
          value={query}
          placeholder="Search messages"
          aria-label="Search conversations"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {visible.length === 0 && (
        <p className="inbox-none">No conversation matches “{query.trim()}”.</p>
      )}

      <ul className="inbox">
        {visible.map((conversation) => {
          const partner = conversation.partner
          const typing = typingIn.has(conversation.id)
          return (
            <li key={conversation.id}>
              <Link
                to={`/messages/${conversation.id}`}
                className={`inbox-row${conversation.id === activeId ? ' active' : ''}${
                  conversation.unread_count > 0 ? ' unread' : ''
                }`}
              >
                <span className="inbox-avatar">
                  <Avatar
                    name={partner.display_name}
                    color={partner.avatar_color}
                    url={partner.avatar_url}
                    size="md"
                  />
                  {partner.is_online && <span className="presence-dot" aria-hidden="true" />}
                </span>
                <div className="inbox-info">
                  <div className="inbox-top">
                    <span className="inbox-name">
                      <DisplayName user={partner} />
                    </span>
                    <RelativeTime iso={conversation.last_message_at} className="inbox-time" />
                  </div>
                  <div className={`inbox-preview${typing ? ' typing' : ''}`}>
                    {typing ? (
                      <span className="inbox-typing">
                        typing
                        <span className="typing-dots" aria-hidden="true">
                          <i />
                          <i />
                          <i />
                        </span>
                      </span>
                    ) : conversation.last_message ? (
                      <>
                        {conversation.last_message.mine && (
                          <span className="inbox-you">You: </span>
                        )}
                        {previewOf(conversation.last_message)}
                      </>
                    ) : (
                      <span className="inbox-you">Say hello</span>
                    )}
                  </div>
                </div>
                {conversation.unread_count > 0 && (
                  <span className="inbox-unread" aria-label={`${conversation.unread_count} unread`}>
                    {conversation.unread_count > 99 ? '99+' : conversation.unread_count}
                  </span>
                )}
              </Link>
            </li>
          )
        })}
      </ul>
    </>
  )
}

/** The forward destination picker. */
function ForwardPicker({
  conversations,
  onPick,
  onClose,
  busy,
}: {
  conversations: Conversation[]
  onPick: (conversationId: number) => void
  onClose: () => void
  busy: boolean
}) {
  return (
    <Modal title="Forward to" onClose={onClose}>
      <p className="forward-hint">Pick a conversation to send a copy of this message to.</p>
      <ul className="forward-list">
        {conversations.map((conversation) => (
          <li key={conversation.id}>
            <button
              type="button"
              className="forward-row"
              disabled={busy}
              onClick={() => onPick(conversation.id)}
            >
              <Avatar
                name={conversation.partner.display_name}
                color={conversation.partner.avatar_color}
                url={conversation.partner.avatar_url}
                size="sm"
              />
              <span className="forward-name">
                <DisplayName user={conversation.partner} />
              </span>
            </button>
          </li>
        ))}
        {conversations.length === 0 && (
          <li className="forward-empty">Start another conversation first, then forward to it.</li>
        )}
      </ul>
    </Modal>
  )
}

/** A single thread with live delivery, typing state and attachments. */
function Thread({
  conversation,
  conversations,
  onSent,
  onTyping,
}: {
  conversation: Conversation
  conversations: Conversation[]
  onSent: () => void
  onTyping: (conversationId: number, typing: boolean) => void
}) {
  const { subscribe, sendTyping } = useRealtime()
  const call = useCall()
  const [messages, setMessages] = useState<Message[]>(
    () => queryCache.read<Message[]>(`messages:${conversation.id}`) ?? [],
  )
  const [loading, setLoading] = useState(
    () => queryCache.read<Message[]>(`messages:${conversation.id}`) === undefined,
  )
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<Media[]>([])
  const [sending, setSending] = useState(false)
  const [partnerTyping, setPartnerTyping] = useState(false)
  const [replyTo, setReplyTo] = useState<Message | null>(null)
  const [editing, setEditing] = useState<Message | null>(null)
  const [stickersOpen, setStickersOpen] = useState(false)
  const [menu, setMenu] = useState<{ message: Message; anchor: DOMRect } | null>(null)
  const [forwarding, setForwarding] = useState<Message | null>(null)
  const [forwardBusy, setForwardBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<Message | null>(null)
  // Live presence for the partner, seeded from the conversation payload and
  // then updated by `presence` frames without refetching the inbox.
  const [partner, setPartner] = useState(conversation.partner)
  // How far the partner has read, so own messages can show "Seen" immediately.
  const [partnerReadAt, setPartnerReadAt] = useState<string | null>(
    conversation.partner_read_at,
  )

  useEffect(() => {
    setPartner(conversation.partner)
    setPartnerReadAt(conversation.partner_read_at)
  }, [conversation.partner, conversation.partner_read_at])

  const scrollRef = useRef<HTMLDivElement>(null)
  const typingTimer = useRef<number | null>(null)
  const lastTypingSent = useRef(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // Presence is shown to the minute, so the header only needs to re-render
  // about that often rather than on every tick.
  const now = useNow(30_000)

  // A thread reopened in this session paints its history at once. Re-reading 50
  // messages costs seconds here, and a reader who steps back into a thread has
  // not asked for anything new: the socket already delivers anything that
  // arrived while they were away.
  const historyKey = `messages:${conversation.id}`
  const load = useCallback(() => {
    setLoading(true)
    queryCache
      .load(historyKey, () => api.messages(conversation.id, 50), 30_000)
      .then((data) => {
        setMessages(data)
        setError(null)
      })
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : 'Could not load messages'),
      )
      .finally(() => setLoading(false))
  }, [conversation.id, historyKey])

  // A receipt that arrived while the history was loading would be overwritten
  // by the fetch, so the loaded page is reconciled with it here.
  useEffect(() => {
    if (!partnerReadAt) return
    setMessages((current) => {
      let changed = false
      const next = current.map((item) => {
        if (!item.mine || item.read_by_partner) return item
        if (new Date(item.created_at) > new Date(partnerReadAt)) return item
        changed = true
        return { ...item, read_by_partner: true }
      })
      if (!changed) return current
      queryCache.write(historyKey, next)
      return next
    })
  }, [historyKey, partnerReadAt, messages.length])

  /**
   * Clear the unread state for this thread, at once and in the background.
   *
   * The badge in the shell drops immediately rather than after the request:
   * opening a thread is the reader saying they have seen it, and waiting for a
   * round trip is what made the count linger until a refresh.
   *
   * The amount to subtract is held in a ref because the count changes as a
   * result of this very call — the inbox reloads with zero unread — and a
   * dependency on it would re-run the mount effect, refetch, and loop.
   */
  const unreadOnOpen = useRef(conversation.unread_count)
  const markRead = useCallback(() => {
    decrement('messages', unreadOnOpen.current)
    unreadOnOpen.current = 0
    void api.markConversationRead(conversation.id).catch(() => undefined)
  }, [conversation.id])

  useEffect(() => {
    unreadOnOpen.current = conversation.unread_count
    load()
    markRead()
    onSent()
    // `conversation.unread_count` is read through the ref above on purpose:
    // including it here would refetch the thread every time a message lands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation.id, load, markRead, onSent])

  // Live messages, edits, deletions, likes and typing for this thread only.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message' && event.message?.conversation_id === conversation.id) {
          setMessages((current) => {
            if (current.some((item) => item.id === event.message!.id)) return current
            const next = [...current, event.message!]
            queryCache.write(historyKey, next)
            return next
          })
          // A message arriving in the thread the reader is looking at is read
          // on arrival, so the inbox must not show it as unread either.
          void api.markConversationRead(conversation.id).catch(() => undefined)
        }
        if (event.type === 'message_edit' && event.message?.conversation_id === conversation.id) {
          const updated = event.message
          setMessages((current) => {
            const next = current.map((item) => (item.id === updated.id ? updated : item))
            queryCache.write(historyKey, next)
            return next
          })
        }
        if (event.type === 'message_delete' && event.conversation_id === conversation.id) {
          setMessages((current) => {
            const next = current.filter((item) => item.id !== event.message_id)
            queryCache.write(historyKey, next)
            return next
          })
          setReplyTo((current) => (current?.id === event.message_id ? null : current))
        }
        if (event.type === 'message_like' && event.conversation_id === conversation.id) {
          setMessages((current) => {
            const next = current.map((item) =>
              item.id === event.message_id
                ? { ...item, like_count: event.like_count ?? item.like_count }
                : item,
            )
            queryCache.write(historyKey, next)
            return next
          })
        }
        if (event.type === 'read' && event.conversation_id === conversation.id) {
          // The partner read this thread: every own message up to that stamp
          // becomes "Seen" without a refetch.
          const readAt = event.last_read_at ?? new Date().toISOString()
          setPartnerReadAt(readAt)
          setMessages((current) => {
            const next = current.map((item) =>
              item.mine && new Date(item.created_at) <= new Date(readAt)
                ? { ...item, read_by_partner: true }
                : item,
            )
            queryCache.write(historyKey, next)
            return next
          })
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
    [subscribe, conversation.id, conversation.partner.id, historyKey],
  )

  // The inbox row shows "typing" too, so the parent is told as it changes.
  useEffect(() => {
    onTyping(conversation.id, partnerTyping)
  }, [onTyping, conversation.id, partnerTyping])

  useEffect(
    () => () => {
      onTyping(conversation.id, false)
      if (typingTimer.current !== null) window.clearTimeout(typingTimer.current)
    },
    [onTyping, conversation.id],
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
  const pinTimer = useRef<number | null>(null)
  // Set when the reader scrolls the thread themselves. Unlike the pin window
  // expiring, this is not revivable by later content: someone who has scrolled
  // up is reading history and must not be pulled back down.
  const userDisarmed = useRef(false)

  /**
   * Keep the newest message in view, and restart the window that decides how
   * long to keep doing so.
   *
   * The window has to be measured from the last time the content actually
   * changed, not from the last render. An attachment is fetched over the
   * network after the bubble is painted, so a slow image can grow the thread
   * several seconds after the messages arrived. With the window started at
   * render time it had already expired by then, the growth was ignored, and the
   * thread settled hundreds of pixels above the newest message — which is the
   * "still opens at the top" report. Re-arming on every growth is what makes a
   * late image land at the bottom too.
   */
  const armPin = useCallback(() => {
    pinned.current = true
    if (pinTimer.current !== null) window.clearTimeout(pinTimer.current)
    pinTimer.current = window.setTimeout(() => {
      pinTimer.current = null
      pinned.current = false
    }, PIN_WINDOW_MS)
  }, [])

  // Only opening a thread re-arms the pin; scrolling up disarms it for good, so
  // arriving messages never fight a reader who is reading history.
  useEffect(() => {
    userDisarmed.current = false
    armPin()
    return () => {
      if (pinTimer.current !== null) {
        window.clearTimeout(pinTimer.current)
        pinTimer.current = null
      }
    }
  }, [conversation.id, armPin])

  /**
   * Keep the newest message in view while the thread's content is still settling.
   *
   * The scroll above runs before the attachments have decoded, and an image
   * holds only a 120px placeholder until then — a tall photo grows the thread
   * by hundreds of pixels afterwards, leaving the view stuck that far above the
   * bottom, which reads as the thread having opened at the top. Re-pinning on
   * growth is what closes that gap.
   *
   * Growth is honoured even after the pin window has lapsed, because that is
   * exactly when a slow attachment lands. Only an explicit scroll by the reader
   * suppresses it.
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
      userDisarmed.current = true
      pinned.current = false
    }
    node.addEventListener('wheel', stop, { passive: true })
    node.addEventListener('touchstart', stop, { passive: true })
    node.addEventListener('keydown', stop)

    const observer = new ResizeObserver(() => {
      const height = node.scrollHeight
      const grew = height - contentHeight.current > GROWTH_TOLERANCE_PX
      contentHeight.current = height
      if (userDisarmed.current || !grew) return
      node.scrollTop = height
      armPin()
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
  }, [conversation.id, messages, partnerTyping, replyTo, armPin])

  /**
   * Add a message the server has confirmed, keeping the cached history in step.
   *
   * Every send path goes through here. Updating only React state would leave
   * the cache holding the pre-send thread, so stepping out and straight back in
   * would drop the message that was just sent.
   */
  function appendMessage(message: Message) {
    setMessages((current) => {
      if (current.some((item) => item.id === message.id)) return current
      const next = [...current, message]
      queryCache.write(historyKey, next)
      return next
    })
  }

  function replaceMessage(message: Message) {
    setMessages((current) => {
      const next = current.map((item) => (item.id === message.id ? message : item))
      queryCache.write(historyKey, next)
      return next
    })
  }

  function startReply(message: Message) {
    setEditing(null)
    setReplyTo(message)
    // Focus the composer so the swipe lands the user where they expect.
    inputRef.current?.focus()
  }

  function startEdit(message: Message) {
    setReplyTo(null)
    setEditing(message)
    setDraft(message.body)
    inputRef.current?.focus()
  }

  function cancelComposeMode() {
    setReplyTo(null)
    setEditing(null)
    setDraft('')
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
    const applyLike = (patch: Partial<Message>) =>
      setMessages((current) => {
        const next = current.map((item) => (item.id === message.id ? { ...item, ...patch } : item))
        queryCache.write(historyKey, next)
        return next
      })
    applyLike({ liked_by_me: optimistic.liked_by_me, like_count: optimistic.like_count })
    try {
      const result = next
        ? await api.likeMessage(conversation.id, message.id)
        : await api.unlikeMessage(conversation.id, message.id)
      applyLike({ liked_by_me: result.liked_by_me, like_count: result.like_count })
    } catch {
      applyLike({ liked_by_me: message.liked_by_me, like_count: message.like_count })
    }
  }

  async function copyMessage(message: Message) {
    try {
      await navigator.clipboard.writeText(message.body)
      flash('Copied')
    } catch {
      flash('Could not copy')
    }
  }

  function flash(text: string) {
    setNotice(text)
    window.setTimeout(() => setNotice((current) => (current === text ? null : current)), 1800)
  }

  async function deleteMessage(message: Message) {
    setConfirmDelete(null)
    const snapshot = messages
    setMessages((current) => {
      const next = current.filter((item) => item.id !== message.id)
      queryCache.write(historyKey, next)
      return next
    })
    try {
      await api.deleteMessage(conversation.id, message.id)
      flash('Message deleted')
      onSent()
    } catch (caught) {
      setMessages(snapshot)
      queryCache.write(historyKey, snapshot)
      setError(caught instanceof ApiError ? caught.message : 'Could not delete the message')
    }
  }

  async function forwardTo(targetId: number) {
    if (!forwarding) return
    setForwardBusy(true)
    try {
      await api.forwardMessage(conversation.id, forwarding.id, targetId)
      setForwarding(null)
      flash('Forwarded')
      onSent()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not forward the message')
    } finally {
      setForwardBusy(false)
    }
  }

  function onAction(action: MessageAction) {
    const message = menu?.message
    setMenu(null)
    if (!message) return
    switch (action) {
      case 'reply':
        startReply(message)
        break
      case 'like':
        void toggleLike(message)
        break
      case 'copy':
        void copyMessage(message)
        break
      case 'edit':
        startEdit(message)
        break
      case 'forward':
        setForwarding(message)
        break
      case 'delete':
        setConfirmDelete(message)
        break
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
    if (editing) {
      if (!body || sending) return
      setSending(true)
      setError(null)
      try {
        const updated = await api.editMessage(conversation.id, editing.id, body)
        replaceMessage(updated)
        setEditing(null)
        setDraft('')
        flash('Message updated')
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : 'Could not save the edit')
      } finally {
        setSending(false)
      }
      return
    }

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
      appendMessage(sent)
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

  /**
   * Send one sticker as its own message.
   *
   * Sent immediately rather than staged in the composer: a sticker is a
   * complete message on its own, and every chat app the reader has used
   * behaves this way. The tray stays open so several can follow in a row.
   */
  async function sendSticker(sticker: Sticker) {
    if (sending) return
    setSending(true)
    setError(null)
    try {
      const sent = await api.sendMessage(conversation.id, '', [], replyTo?.id ?? null, sticker.id)
      appendMessage(sent)
      setReplyTo(null)
      onSent()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Sticker not sent')
    } finally {
      setSending(false)
    }
  }

  const canSend = editing
    ? draft.trim().length > 0 && !sending
    : (draft.trim().length > 0 || attachments.length > 0) && !sending

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
      appendMessage(sent)
      setReplyTo(null)
      onSent()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Voice note not sent')
    } finally {
      setSending(false)
    }
  }

  const callActive = call.state.phase !== 'idle' && call.state.phase !== 'ended'

  function startCall(mode: 'audio' | 'video') {
    if (callActive) return
    void call.start({
      conversationId: conversation.id,
      mode,
      peerName: partner.display_name,
      peerColor: partner.avatar_color,
      peerAvatar: partner.avatar_url,
    })
  }

  /** Grouping: same sender, and close enough in time to read as one turn. */
  const rendered = useMemo(() => {
    return messages.map((message, index) => {
      const previous = messages[index - 1]
      const grouped = Boolean(
        previous &&
          previous.sender.id === message.sender.id &&
          new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() <
            GROUP_WINDOW_MS,
      )
      const showDay =
        !previous || dayKey(previous.created_at) !== dayKey(message.created_at)
      return { message, grouped, showDay }
    })
  }, [messages])

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
          <div className="thread-identity">
            <div className="thread-name">
              <DisplayName user={partner} />
            </div>
            <div
              className={`thread-handle${partner.is_online ? ' online' : ''}${
                partnerTyping ? ' typing' : ''
              }`}
            >
              {partnerTyping ? 'typing…' : formatPresence(partner.last_seen_at, partner.is_online, now)}
            </div>
          </div>
        </Link>
        <div className="thread-call-actions">
          <button
            type="button"
            className="icon-btn thread-call-btn"
            aria-label={`Audio call ${partner.display_name}`}
            title="Audio call"
            onClick={() => startCall('audio')}
            disabled={callActive}
          >
            <PhoneIcon size={19} />
          </button>
          <button
            type="button"
            className="icon-btn thread-call-btn"
            aria-label={`Video call ${partner.display_name}`}
            title="Video call"
            onClick={() => startCall('video')}
            disabled={callActive}
          >
            <VideoIcon size={19} />
          </button>
        </div>
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

        {rendered.map(({ message, grouped, showDay }) => (
          <div key={message.id} className="thread-item">
            {showDay && (
              <div className="thread-day">
                <span>{dayLabel(message.created_at, now)}</span>
              </div>
            )}
            <MessageBubble
              message={message}
              grouped={grouped}
              partnerOnline={Boolean(partner.is_online)}
              onReply={startReply}
              onToggleLike={toggleLike}
              onOpenMenu={(target, anchor) => setMenu({ message: target, anchor })}
            />
          </div>
        ))}

        {partnerTyping && (
          <div className="bubble-row">
            <div className="bubble typing-bubble" aria-live="polite" aria-label="Typing">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        )}
      </div>

      {notice && <p className="thread-notice">{notice}</p>}
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

      {editing && (
        <div className="reply-preview editing">
          <ReplyIcon size={15} />
          <div className="reply-preview-body">
            <span className="reply-preview-name">Editing message</span>
            <span className="reply-preview-text">{editing.body}</span>
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="Cancel edit"
            onClick={cancelComposeMode}
          >
            <CloseIcon size={15} />
          </button>
        </div>
      )}

      <StickerPicker
        open={stickersOpen}
        disabled={sending}
        onPick={sendSticker}
        onClose={() => setStickersOpen(false)}
      />

      <form className="composer-inline thread-composer" onSubmit={submit}>
        {!editing && (
          <MediaPicker items={attachments} onChange={setAttachments} disabled={sending} />
        )}
        {!editing && (
          <button
            type="button"
            className={`icon-btn sticker-toggle${stickersOpen ? ' active' : ''}`}
            aria-label={stickersOpen ? 'Hide stickers' : 'Show stickers'}
            aria-expanded={stickersOpen}
            onClick={() => setStickersOpen((open) => !open)}
          >
            <StickerIcon size={19} />
          </button>
        )}
        {!editing && (
          <VoiceRecorderButton disabled={sending} onRecorded={sendVoiceNote} onError={setError} />
        )}
        <textarea
          ref={inputRef}
          className="input thread-input"
          placeholder={editing ? 'Edit your message' : `Message @${conversation.partner.username}`}
          value={draft}
          rows={1}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void submit(event)
            }
            if (event.key === 'Escape' && editing) {
              cancelComposeMode()
            }
          }}
          aria-label={editing ? 'Edit message' : 'Message'}
        />
        <button
          type="submit"
          className="btn btn-primary btn-icon"
          disabled={!canSend}
          aria-label={editing ? 'Save edit' : 'Send message'}
        >
          {sending ? <Spinner size={16} /> : <SendIcon size={18} />}
        </button>
      </form>

      {menu && (
        <MessageActionSheet
          message={menu.message}
          anchor={menu.anchor}
          onAction={onAction}
          onClose={() => setMenu(null)}
        />
      )}

      {forwarding && (
        <ForwardPicker
          conversations={conversations.filter((item) => item.id !== conversation.id)}
          busy={forwardBusy}
          onPick={(targetId) => void forwardTo(targetId)}
          onClose={() => setForwarding(null)}
        />
      )}

      {confirmDelete && (
        <Modal
          title="Delete message?"
          onClose={() => setConfirmDelete(null)}
          footer={
            <>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setConfirmDelete(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => void deleteMessage(confirmDelete)}
              >
                Delete
              </button>
            </>
          }
        >
          <p>
            This removes the message for both of you. It cannot be undone.
          </p>
        </Modal>
      )}
    </div>
  )
}

export function MessagesPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()

  const { subscribe } = useRealtime()

  const [conversations, setConversations] = useState<Conversation[]>(
    () => queryCache.read<Conversation[]>(INBOX_KEY) ?? [],
  )
  const [loading, setLoading] = useState(
    () => queryCache.read<Conversation[]>(INBOX_KEY) === undefined,
  )
  const [error, setError] = useState<string | null>(null)
  const [typingIn, setTypingIn] = useState<Set<number>>(new Set())

  const activeId = conversationId ? Number(conversationId) : undefined

  const load = useCallback((force = false) => {
    // No spinner once there is something to look at: a live message reorders
    // this list, and blanking it to refetch would flash the inbox away.
    if (queryCache.read<Conversation[]>(INBOX_KEY) === undefined) setLoading(true)
    queryCache
      .load(INBOX_KEY, () => api.conversations(), 30_000, force)
      .then(setConversations)
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : 'Could not load conversations'),
      )
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Stable identity: `Thread` calls this from a mount effect, so a new function
  // per render would re-run that effect on every render and refetch the inbox
  // in a loop.
  const refreshInbox = useCallback(() => load(true), [load])

  /** Track which threads have a partner typing, for the inbox rows. */
  const setTyping = useCallback((id: number, typing: boolean) => {
    setTypingIn((current) => {
      if (typing === current.has(id)) return current
      const next = new Set(current)
      if (typing) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  // Any incoming message reorders the inbox.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message') load()
        if (event.type === 'typing' && typeof event.conversation_id === 'number') {
          const id = event.conversation_id
          setTyping(id, true)
          window.setTimeout(() => setTyping(id, false), 4000)
        }
      }),
    [subscribe, load, setTyping],
  )

  const active = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId),
    [conversations, activeId],
  )

  // On a narrow screen the inbox and the thread take turns, so the URL drives
  // which one is visible.
  const showThread = Boolean(activeId)

  /**
   * Drop a thread's unread badge the moment it is opened.
   *
   * `Thread` clears the server's copy and the shell's count; this keeps the
   * inbox row itself in step, so stepping back out shows no badge rather than
   * waiting for the next fetch.
   */
  useEffect(() => {
    if (!activeId) return
    setConversations((current) => {
      const row = current.find((item) => item.id === activeId)
      if (!row || row.unread_count === 0) return current
      const next = current.map((item) =>
        item.id === activeId ? { ...item, unread_count: 0 } : item,
      )
      queryCache.write(INBOX_KEY, next)
      return next
    })
  }, [activeId])

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
            typingIn={typingIn}
          />
        </div>
        <div className="messages-thread">
          {active ? (
            <Thread
              conversation={active}
              conversations={conversations}
              onSent={refreshInbox}
              onTyping={setTyping}
            />
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
