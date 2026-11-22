import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar, DisplayName } from '../components/Avatar'
import { MediaPicker } from '../components/MediaPicker'
import { VoiceRecorderButton, voiceNoteFilename } from '../components/VoiceRecorderButton'
import { MessageBubble } from '../components/MessageBubble'
import { CallEntry } from '../components/CallEntry'
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
import { useAuth } from '../context/AuthContext'
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

/**
 * How long a cached thread is painted without asking the server again.
 *
 * Short on purpose. A thread is where a missing message is most visible, and
 * the cache is filled by whoever last read it: stepping into a thread that a
 * partner wrote to while the inbox was open must not show the version from
 * before that message. Cached messages still paint immediately — this only
 * decides when the background re-read runs.
 */
const HISTORY_TTL_MS = 5_000

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

/**
 * Fold a freshly read page into the thread already on screen.
 *
 * A message can arrive over the socket while the history request is in flight,
 * and the response — read before it existed — would drop it from the list.
 * Anything newer than the page's last row is kept; older rows the page does not
 * mention are dropped, so a message deleted elsewhere does not come back.
 */
function mergeHistory(current: Message[], fetched: Message[]): Message[] {
  if (current.length === 0) return fetched
  const known = new Set(fetched.map((item) => item.id))
  const newest = fetched.length
    ? new Date(fetched[fetched.length - 1].created_at).getTime()
    : 0
  const conversationId = fetched[0]?.conversation_id
  const arrivedMeanwhile = current.filter(
    (item) =>
      !known.has(item.id) &&
      (conversationId === undefined || item.conversation_id === conversationId) &&
      new Date(item.created_at).getTime() > newest,
  )
  if (arrivedMeanwhile.length === 0) return fetched
  return [...fetched, ...arrivedMeanwhile].sort(
    (left, right) =>
      new Date(left.created_at).getTime() - new Date(right.created_at).getTime() ||
      left.id - right.id,
  )
}

/**
 * Whether an optimistic bubble stands for the message that just arrived.
 *
 * Matched on what the sender chose — text, sticker, attachments and the message
 * replied to — because the ids are only known once the server has answered.
 */
function sameDraft(sent: Message, incoming: Message): boolean {
  if (sent.body !== incoming.body) return false
  if ((sent.sticker ?? null) !== (incoming.sticker ?? null)) return false
  if ((sent.reply_to?.id ?? null) !== (incoming.reply_to?.id ?? null)) return false
  if (sent.media.length !== incoming.media.length) return false
  return sent.media.every((item, index) => item.id === incoming.media[index]?.id)
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
  // Re-renders the previews on a clock, so a call that has ended stops reading
  // as "Calling…" in the inbox without waiting for a reload.
  const now = useNow(15_000)

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
                        {previewOf(conversation.last_message, now)}
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
  const { subscribe, sendTyping, connectionEpoch } = useRealtime()
  const { user } = useAuth()
  const call = useCall()
  const [messages, setMessages] = useState<Message[]>(() => {
    const cached = queryCache.read<Message[]>(`messages:${conversation.id}`) ?? []
    // The inbox row carries the newest message the server has for this thread.
    // When it is newer than the cached history, a message arrived after this
    // thread was last read, and painting the cache alone would show a thread
    // missing it until the re-read lands.
    const newest = conversation.last_message
    if (!newest || cached.length === 0) return cached
    if (cached.some((item) => item.id === newest.id)) return cached
    if (newest.id < cached[cached.length - 1].id) return cached
    return [...cached, newest]
  })
  const [loading, setLoading] = useState(
    () => queryCache.read<Message[]>(`messages:${conversation.id}`) === undefined,
  )
  // Bubbles for messages this device has sent that the server has not confirmed
  // yet. They are kept out of `messages` on purpose: the history cache must only
  // ever hold confirmed rows, or a pending bubble would come back after a
  // navigation and never resolve.
  const [pending, setPending] = useState<Message[]>([])

  /**
   * What the thread draws: confirmed history plus anything still in flight.
   *
   * The pending rows are last because they are the newest by definition, and
   * they are not in the cache, so a navigation cannot resurrect them.
   */
  const visible = useMemo(
    () => (pending.length > 0 ? [...messages, ...pending] : messages),
    [messages, pending],
  )
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draft, setDraftState] = useState('')
  // The draft is mirrored here because a send clears it outside React's
  // render: a second Enter in the same tick would otherwise still see the old
  // text and send it twice.
  const draftRef = useRef('')
  const [attachments, setAttachments] = useState<Media[]>([])
  const [savingEdit, setSavingEdit] = useState(false)
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

  // A thread reopened in this session paints its history at once, then
  // revalidates in the background. Painting only the cache is what left a
  // partner's latest message invisible until the cache expired: the socket
  // delivers messages that arrive while this page is mounted, but not one sent
  // while the reader was on another screen.
  const historyKey = `messages:${conversation.id}`
  const load = useCallback(
    (force = false) => {
      if (!force && queryCache.read<Message[]>(historyKey) === undefined) setLoading(true)
      queryCache
        .load(historyKey, () => api.messages(conversation.id, 50), HISTORY_TTL_MS, force)
        .then((data) => {
          setMessages((current) => {
            const merged = mergeHistory(current, data)
            queryCache.write(historyKey, merged)
            return merged
          })
          setError(null)
        })
        .catch((caught) =>
          setError(caught instanceof ApiError ? caught.message : 'Could not load messages'),
        )
        .finally(() => setLoading(false))
    },
    [conversation.id, historyKey],
  )

  // The inbox re-read can land after the thread is open, and it carries the
  // newest message the server has. Folding it in is what makes a message sent
  // while the reader was on another screen appear at once, without waiting for
  // the history request.
  useEffect(() => {
    const newest = conversation.last_message
    if (!newest) return
    setMessages((current) => {
      if (current.length === 0 || current.some((item) => item.id === newest.id)) return current
      if (newest.id < current[current.length - 1].id) return current
      const next = [...current, newest]
      queryCache.write(historyKey, next)
      return next
    })
  }, [conversation.last_message, historyKey])

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

  // A socket that was down may have missed messages entirely, so the open
  // thread is re-read as soon as the connection is back.
  useEffect(() => {
    if (connectionEpoch === 0) return
    load(true)
  }, [connectionEpoch, load])

  // Live messages, edits, deletions, likes and typing for this thread only.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message' && event.message?.conversation_id === conversation.id) {
          const incoming = event.message
          // Our own message comes back over the socket too. When it does, the
          // optimistic bubble standing in for it is dropped here; the row that
          // follows is the confirmed one, so the thread never shows both.
          if (incoming.mine) {
            setPending((current) => {
              const index = current.findIndex((item) => sameDraft(item, incoming))
              if (index === -1) return current
              return current.filter((_, position) => position !== index)
            })
          }
          setMessages((current) => {
            // A call entry is created when the call starts and updated when it
            // ends, so the same id arrives more than once. Replacing it in
            // place is what turns "Calling…" into a duration without a reload.
            const existing = current.findIndex((item) => item.id === incoming.id)
            const next =
              existing === -1
                ? [...current, incoming]
                : current.map((item, index) => (index === existing ? incoming : item))
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
  }, [visible, partnerTyping, replyTo, conversation.id])

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
  }, [conversation.id, visible, partnerTyping, replyTo, armPin])

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

  // Mirrors `pending` so an async failure can tell whether the bubble it is
  // about to remove is still there, or whether the socket already confirmed it.
  const pendingRef = useRef<Message[]>([])
  useEffect(() => {
    pendingRef.current = pending
  }, [pending])

  const pendingSeq = useRef(0)
  // Object URLs belonging to pending bubbles, so they are released when the
  // confirmed row replaces them. A voice note is drawn from the local
  // recording until its upload lands.
  const pendingUrls = useRef(new Map<number, string>())

  function setDraft(value: string) {
    draftRef.current = value
    setDraftState(value)
  }

  // Grow the field to fit a long draft, up to a cap, so the line being typed
  // stays visible instead of scrolling out of a one-line box.
  useEffect(() => {
    const element = inputRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 140)}px`
  }, [draft])

  /**
   * The bubble drawn while a send is in flight.
   *
   * The server's row replaces it the moment it lands. Until then this is what
   * makes the send feel instant instead of waiting on the round trip.
   */
  function optimisticMessage(input: {
    body: string
    media: Media[]
    sticker: string | null
    replyTo: Message | null
  }): Message | null {
    if (!user) return null
    pendingSeq.current += 1
    return {
      id: -pendingSeq.current,
      conversation_id: conversation.id,
      body: input.body,
      created_at: new Date().toISOString(),
      sender: user,
      media: input.media,
      mine: true,
      sticker: input.sticker,
      reply_to: input.replyTo
        ? {
            id: input.replyTo.id,
            body: input.replyTo.body,
            sender_username: input.replyTo.sender.username,
            sender_display_name: input.replyTo.sender.display_name,
            has_media: input.replyTo.media.length > 0,
          }
        : null,
      like_count: 0,
      liked_by_me: false,
      edited_at: null,
      forwarded: false,
      read_by_partner: false,
      call: null,
      pending: true,
    }
  }

  function dropPending(id: number) {
    const url = pendingUrls.current.get(id)
    if (url) {
      pendingUrls.current.delete(id)
      URL.revokeObjectURL(url)
    }
    setPending((current) => current.filter((item) => item.id !== id))
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
    const body = draftRef.current.trim()
    if (editing) {
      if (!body || savingEdit) return
      setSavingEdit(true)
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
        setSavingEdit(false)
      }
      return
    }

    if (!body && attachments.length === 0) return

    const media = attachments
    const quoting = replyTo
    const optimistic = optimisticMessage({ body, media, sticker: null, replyTo: quoting })

    setError(null)
    if (optimistic) setPending((current) => [...current, optimistic])
    // The composer empties at once: the message is already on screen, and
    // holding the text until the round trip finished is what made sending feel
    // like it had stalled.
    setDraft('')
    setAttachments([])
    setReplyTo(null)
    try {
      const sent = await api.sendMessage(
        conversation.id,
        body,
        media.map((item) => item.id),
        quoting?.id ?? null,
      )
      appendMessage(sent)
      if (optimistic) dropPending(optimistic.id)
      onSent()
    } catch (caught) {
      // Only give the text back if this bubble is still unsent: when the socket
      // already delivered the message the request did land, and restoring the
      // draft would duplicate it.
      if (optimistic && pendingRef.current.some((item) => item.id === optimistic.id)) {
        dropPending(optimistic.id)
        setDraft(body)
        setAttachments(media)
        setReplyTo(quoting)
      }
      setError(caught instanceof ApiError ? caught.message : 'Message not sent')
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
    const quoting = replyTo
    const optimistic = optimisticMessage({ body: '', media: [], sticker: sticker.id, replyTo: quoting })

    setError(null)
    if (optimistic) setPending((current) => [...current, optimistic])
    setReplyTo(null)
    try {
      const sent = await api.sendMessage(conversation.id, '', [], quoting?.id ?? null, sticker.id)
      appendMessage(sent)
      if (optimistic) dropPending(optimistic.id)
      onSent()
    } catch (caught) {
      if (optimistic && pendingRef.current.some((item) => item.id === optimistic.id)) {
        dropPending(optimistic.id)
        setReplyTo(quoting)
      }
      setError(caught instanceof ApiError ? caught.message : 'Sticker not sent')
    }
  }

  // Only content decides whether send is available. A message in flight must
  // never disable it: the next one is allowed to leave while the last is still
  // being confirmed, and each bubble carries its own status.
  const canSend = editing
    ? draft.trim().length > 0 && !savingEdit
    : draft.trim().length > 0 || attachments.length > 0

  /**
   * Upload a finished recording and send it as the next message.
   *
   * The clip is uploaded first and then referenced by id, which is the same
   * path an attached file takes, so a voice note needs no separate storage or
   * delivery route. The bubble goes up before the upload starts, playing from
   * the local recording, so the sender can hear it while the bytes travel.
   */
  async function sendVoiceNote(note: VoiceNote) {
    const quoting = replyTo
    const localUrl = URL.createObjectURL(note.blob)
    const local: Media = {
      id: -Date.now(),
      kind: 'audio',
      content_type: note.mimeType,
      size_bytes: note.blob.size,
      width: null,
      height: null,
      alt_text: 'Voice note',
      url: localUrl,
      poster_url: null,
      duration_ms: Math.round(note.duration * 1000),
    }
    const optimistic = optimisticMessage({
      body: '',
      media: [local],
      sticker: null,
      replyTo: quoting,
    })

    setError(null)
    if (optimistic) {
      pendingUrls.current.set(optimistic.id, localUrl)
      setPending((current) => [...current, optimistic])
    } else {
      URL.revokeObjectURL(localUrl)
    }
    setReplyTo(null)
    try {
      // A `File` rather than the raw blob: the upload path names the part from
      // it, and the server sniffs the bytes, so the name only has to carry the
      // right container.
      const file = new File([note.blob], voiceNoteFilename(note), { type: note.mimeType })
      // The recorder already knows how long the clip is. Sending it means the
      // bubble can show the length immediately, instead of waiting for the
      // audio element to decode metadata it may never fetch.
      const media = await api.upload(file, 'Voice note', undefined, null, note.duration * 1000)
      const sent = await api.sendMessage(
        conversation.id,
        '',
        [media.id],
        quoting?.id ?? null,
      )
      appendMessage(sent)
      if (optimistic) dropPending(optimistic.id)
      onSent()
    } catch (caught) {
      if (optimistic && pendingRef.current.some((item) => item.id === optimistic.id)) {
        dropPending(optimistic.id)
        setReplyTo(quoting)
      }
      setError(caught instanceof ApiError ? caught.message : 'Voice note not sent')
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
    return visible.map((message, index) => {
      const previous = visible[index - 1]
      // A call entry is a centred event, not a bubble, so nothing groups with
      // it: a message that followed one would otherwise draw without an avatar
      // and read as a continuation of the call.
      const grouped = Boolean(
        previous &&
          !previous.call &&
          !message.call &&
          previous.sender.id === message.sender.id &&
          new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() <
            GROUP_WINDOW_MS,
      )
      const showDay =
        !previous || dayKey(previous.created_at) !== dayKey(message.created_at)
      return { message, grouped, showDay }
    })
  }, [visible])

  return (
    <div className="thread">
      <header className="thread-head">
        {/* On a phone the thread owns the screen, so it carries its own way
            back rather than leaving that to the page header above it, which is
            hidden while a thread is open. On a desktop the page header keeps
            its own back button and this one stays out of the way. */}
        <Link to="/messages" className="icon-btn thread-back-inline" aria-label="Back to inbox">
          <ChevronLeftIcon size={20} />
        </Link>
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
        {!loading && error && visible.length === 0 && (
          <ErrorState message={error} onRetry={() => load(true)} />
        )}
        {!loading && visible.length === 0 && (
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
            {message.call ? (
              <CallEntry
                message={message}
                onOpenMenu={(target, anchor) => setMenu({ message: target, anchor })}
              />
            ) : (
              <MessageBubble
                message={message}
                grouped={grouped}
                onReply={startReply}
                onToggleLike={toggleLike}
                onOpenMenu={(target, anchor) => setMenu({ message: target, anchor })}
              />
            )}
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
      {error && visible.length > 0 && <p className="thread-error">{error}</p>}

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
        onPick={sendSticker}
        onClose={() => setStickersOpen(false)}
      />

      <form className="thread-composer" onSubmit={submit}>
        {!editing && attachments.length > 0 && (
          <div className="composer-strip">
            <MediaPicker items={attachments} onChange={setAttachments} showThumbsOnly />
          </div>
        )}

        <div className={`composer-shell${editing ? ' editing' : ''}`}>
          {!editing && (
            <div className="thread-actions">
              <MediaPicker
                items={attachments}
                onChange={setAttachments}
                onError={setError}
                showTriggerOnly
              />
              <button
                type="button"
                className={`icon-btn sticker-toggle${stickersOpen ? ' active' : ''}`}
                aria-label={stickersOpen ? 'Hide stickers' : 'Show stickers'}
                aria-expanded={stickersOpen}
                onClick={() => setStickersOpen((open) => !open)}
              >
                <StickerIcon size={19} />
              </button>
              <VoiceRecorderButton onRecorded={sendVoiceNote} onError={setError} />
            </div>
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
            className="composer-send"
            disabled={!canSend}
            aria-label={editing ? 'Save edit' : 'Send message'}
          >
            <SendIcon size={20} />
          </button>
        </div>
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

  const { subscribe, connectionEpoch } = useRealtime()

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

  // Revalidated on mount rather than served from the cache: this list carries
  // each thread's newest message, and the thread reads it to paint itself
  // complete before its own history request lands.
  useEffect(() => {
    load(true)
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

  // Any incoming message reorders the inbox, and makes the cached copy of that
  // thread wrong: it predates the message, so opening it would show a thread
  // missing its newest row. The inbox is re-read rather than patched so the
  // preview, order and unread count all come from the server.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'message') {
          const id = event.message?.conversation_id
          // The open thread keeps its own copy in step; only the others are
          // stale.
          if (typeof id === 'number' && id !== activeId) {
            queryCache.invalidate(`messages:${id}`)
          }
          load(true)
        }
        if (event.type === 'typing' && typeof event.conversation_id === 'number') {
          const id = event.conversation_id
          setTyping(id, true)
          window.setTimeout(() => setTyping(id, false), 4000)
        }
      }),
    [subscribe, load, setTyping, activeId],
  )

  // A socket that was down delivered nothing, so the inbox is re-read from the
  // server as soon as it is back rather than trusting what was cached.
  useEffect(() => {
    if (connectionEpoch === 0) return
    load(true)
  }, [connectionEpoch, load])

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
              // Remount per thread: the composer, the optimistic bubbles and the
              // scroll position all belong to one conversation, and carrying
              // them across would put a draft in front of the wrong person.
              key={active.id}
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
