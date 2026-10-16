import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Avatar, DisplayName } from './Avatar'
import { PostText } from './PostCard'
import { RelativeTime } from './UI'
import { HeartIcon, ReplyIcon, TrashIcon } from './Icons'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'
import { formatCount } from '../lib/format'
import type { Comment } from '../lib/types'

/** Depth beyond which replies stop indenting, so deep threads stay readable. */
const MAX_INDENT_DEPTH = 2

interface CommentNodeProps {
  comment: Comment
  postId: number
  postAuthorId: number
  depth?: number
  /** The comment currently being replied to, if any. */
  replyingTo: number | null
  onReplyTarget: (comment: Comment | null) => void
  onReplied: (parentId: number, reply: Comment) => void
  onDeleted: (comment: Comment) => void
  onLikeToggled: (comment: Comment) => void
}

function CommentNode({
  comment,
  postId,
  postAuthorId,
  depth = 0,
  replyingTo,
  onReplyTarget,
  onReplied,
  onDeleted,
  onLikeToggled,
}: CommentNodeProps) {
  const { user } = useAuth()
  const { notify } = useToast()
  const [liking, setLiking] = useState(false)

  const isReplying = replyingTo === comment.id
  const canDelete = user?.id === comment.author.id || user?.id === postAuthorId

  async function toggleLike() {
    if (!user) {
      notify('Sign in to like a reply', 'error')
      return
    }
    if (liking) return
    setLiking(true)
    // Flip the count immediately so the button responds at once, then replace
    // it with the server's answer (or roll back if the request fails).
    const optimistic: Comment = {
      ...comment,
      liked_by_me: !comment.liked_by_me,
      like_count: comment.like_count + (comment.liked_by_me ? -1 : 1),
    }
    onLikeToggled(optimistic)
    try {
      const updated = comment.liked_by_me
        ? await api.unlikeComment(comment.id)
        : await api.likeComment(comment.id)
      onLikeToggled(updated)
    } catch (caught) {
      onLikeToggled(comment)
      notify(caught instanceof Error ? caught.message : 'Could not update the like', 'error')
    } finally {
      setLiking(false)
    }
  }

  async function remove() {
    try {
      await api.deleteComment(comment.id)
      onDeleted(comment)
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : 'Could not delete the reply', 'error')
    }
  }

  return (
    <div className={`comment-thread${depth > 0 ? ' is-reply' : ''}`}>
      <div className="comment">
        <Link to={`/u/${comment.author.username}`}>
          <Avatar
            name={comment.author.display_name}
            color={comment.author.avatar_color}
            url={comment.author.avatar_url}
            size="sm"
          />
        </Link>
        <div className="comment-main">
          <div className="comment-head">
            <Link to={`/u/${comment.author.username}`} className="post-author">
              <DisplayName user={comment.author} />
            </Link>
            <span className="post-handle">@{comment.author.username}</span>
            <span className="post-dot">·</span>
            <RelativeTime iso={comment.created_at} className="post-time" />
            {canDelete && (
              <div style={{ marginLeft: 'auto' }}>
                <button
                  type="button"
                  className="icon-btn danger"
                  aria-label="Delete reply"
                  onClick={() => void remove()}
                >
                  <TrashIcon size={15} />
                </button>
              </div>
            )}
          </div>

          <PostText body={comment.body} className="comment-body" />

          <div className="comment-actions">
            <button
              type="button"
              className={`comment-action${comment.liked_by_me ? ' liked' : ''}`}
              onClick={() => void toggleLike()}
              aria-pressed={comment.liked_by_me}
              aria-label={comment.liked_by_me ? 'Unlike this reply' : 'Like this reply'}
            >
              <HeartIcon size={15} filled={comment.liked_by_me} />
              {comment.like_count > 0 && <span>{formatCount(comment.like_count)}</span>}
            </button>
            <button
              type="button"
              className={`comment-action${isReplying ? ' active' : ''}`}
              onClick={() => onReplyTarget(isReplying ? null : comment)}
              aria-expanded={isReplying}
            >
              <ReplyIcon size={15} />
              <span>Reply</span>
            </button>
          </div>

          {isReplying && (
            <ReplyBox
              postId={postId}
              comment={comment}
              onCancel={() => onReplyTarget(null)}
              onReplied={(reply) => {
                onReplied(comment.id, reply)
                onReplyTarget(null)
              }}
            />
          )}

          {comment.replies.length > 0 && (
            <div className="comment-replies">
              {comment.replies.map((reply) => (
                <CommentNode
                  key={reply.id}
                  comment={reply}
                  postId={postId}
                  postAuthorId={postAuthorId}
                  depth={Math.min(depth + 1, MAX_INDENT_DEPTH)}
                  replyingTo={replyingTo}
                  onReplyTarget={onReplyTarget}
                  onReplied={onReplied}
                  onDeleted={onDeleted}
                  onLikeToggled={onLikeToggled}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** The small composer shown inline under the comment being answered. */
function ReplyBox({
  postId,
  comment,
  onCancel,
  onReplied,
}: {
  postId: number
  comment: Comment
  onCancel: () => void
  onReplied: (reply: Comment) => void
}) {
  const { user } = useAuth()
  const { notify } = useToast()
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)

  if (!user) return null

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!draft.trim() || sending) return
    setSending(true)
    try {
      const created = await api.addComment(postId, draft.trim(), comment.id)
      onReplied(created)
      setDraft('')
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : 'Could not post your reply', 'error')
    } finally {
      setSending(false)
    }
  }

  return (
    <form className="comment-form is-reply-form" onSubmit={submit}>
      <Avatar name={user.display_name} color={user.avatar_color} url={user.avatar_url} size="sm" />
      <div className="comment-form-inner">
        <textarea
          value={draft}
          placeholder={`Reply to @${comment.author.username}`}
          maxLength={1000}
          autoFocus
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') onCancel()
          }}
        />
        <div className="comment-form-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary btn-sm" disabled={!draft.trim() || sending}>
            {sending ? 'Sending…' : 'Reply'}
          </button>
        </div>
      </div>
    </form>
  )
}

export { CommentNode }
