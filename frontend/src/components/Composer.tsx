import { useEffect, useRef, useState } from 'react'
import { Avatar } from './Avatar'
import { MediaPicker } from './MediaPicker'
import { SparkIcon } from './Icons'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'
import type { Media, Post } from '../lib/types'

const MAX_LENGTH = 2000

export function Composer({
  onPosted,
  placeholder = "What's happening?",
  autoFocus = false,
}: {
  onPosted: (post: Post) => void
  placeholder?: string
  autoFocus?: boolean
}) {
  const { user } = useAuth()
  const { notify } = useToast()
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<Media[]>([])
  const [busy, setBusy] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Grow the textarea to fit its content.
  useEffect(() => {
    const element = textareaRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 320)}px`
  }, [body])

  if (!user) return null

  const remaining = MAX_LENGTH - body.length
  const trimmed = body.trim()
  const canSubmit = (trimmed.length > 0 || attachments.length > 0) && remaining >= 0 && !busy

  async function submit() {
    if (!canSubmit) return
    setBusy(true)
    try {
      const post = await api.createPost(
        trimmed,
        attachments.map((item) => item.id),
      )
      onPosted(post)
      setBody('')
      setAttachments([])
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Could not publish your post', 'error')
    } finally {
      setBusy(false)
    }
  }

  const ratio = Math.min(body.length / MAX_LENGTH, 1)
  const tone = remaining < 0 ? 'over' : remaining < 100 ? 'warn' : ''

  return (
    <div className="composer">
      <Avatar name={user.display_name} color={user.avatar_color} size="md" />
      <div className="composer-body">
        <textarea
          ref={textareaRef}
          value={body}
          autoFocus={autoFocus}
          placeholder={placeholder}
          maxLength={MAX_LENGTH + 200}
          aria-label="Post text"
          onChange={(event) => setBody(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault()
              void submit()
            }
          }}
        />
        {attachments.length > 0 && (
          <div className="composer-attachments">
            <MediaPicker items={attachments} onChange={setAttachments} disabled={busy} showThumbsOnly />
          </div>
        )}
        <div className="composer-actions">
          <div className="composer-meta">
            {attachments.length === 0 && (
              <MediaPicker items={attachments} onChange={setAttachments} disabled={busy} />
            )}
            {body.length > MAX_LENGTH - 200 && (
              <span className={`char-count ${tone}`}>{remaining}</span>
            )}
            {body.length > MAX_LENGTH - 200 && (
              <ProgressRing ratio={ratio} over={remaining < 0} />
            )}
            <span className="composer-hint">
              <SparkIcon size={14} /> #hashtags and @mentions work
            </span>
          </div>
          <button type="button" className="btn btn-primary" disabled={!canSubmit} onClick={submit}>
            {busy ? 'Posting…' : 'Post'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ProgressRing({ ratio, over }: { ratio: number; over: boolean }) {
  const radius = 9
  const circumference = 2 * Math.PI * radius
  return (
    <svg className="progress-ring" width={22} height={22} aria-hidden="true">
      <circle cx={11} cy={11} r={radius} fill="none" stroke="var(--surface-3)" strokeWidth={2.5} />
      <circle
        cx={11}
        cy={11}
        r={radius}
        fill="none"
        stroke={over ? 'var(--rose)' : ratio > 0.9 ? '#fbbf24' : 'var(--accent)'}
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - ratio)}
      />
    </svg>
  )
}
