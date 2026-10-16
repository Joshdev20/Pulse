import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar, DisplayName } from '../components/Avatar'
import { PostText } from '../components/PostCard'
import { MediaGallery } from '../components/MediaGallery'
import { CommentNode } from '../components/CommentThread'
import { EditPostModal } from '../components/EditPostModal'
import { EmptyState, ErrorState, FeedSkeleton, Spinner, Dropdown, RelativeTime } from '../components/UI'
import {
  ArrowLeftIcon,
  CommentIcon,
  HeartIcon,
  MoreIcon,
  ShareIcon,
  TrashIcon,
  EditIcon,
} from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'
import { formatCount, formatFullDate } from '../lib/format'
import type { Comment, Post } from '../lib/types'

export function PostDetailPage() {
  const { postId } = useParams()
  const id = Number(postId)
  const navigate = useNavigate()
  const { user } = useAuth()
  const { notify } = useToast()

  const [post, setPost] = useState<Post | null>(null)
  const [comments, setComments] = useState<Comment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  const [pop, setPop] = useState(false)
  const [editing, setEditing] = useState(false)
  const [replyingTo, setReplyingTo] = useState<Comment | null>(null)

  useEffect(() => {
    if (!Number.isFinite(id)) {
      setError('That post link is not valid')
      setLoading(false)
      return
    }
    let active = true
    setLoading(true)
    setError(null)
    Promise.all([api.getPost(id), api.comments(id)])
      .then(([postData, commentData]) => {
        if (!active) return
        setPost(postData)
        setComments(commentData)
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Could not load this post')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [id])

  async function toggleLike() {
    if (!post) return
    if (!user) {
      navigate('/login')
      return
    }
    if (busy) return
    setBusy(true)
    setPost({
      ...post,
      liked_by_me: !post.liked_by_me,
      like_count: post.like_count + (post.liked_by_me ? -1 : 1),
    })
    if (!post.liked_by_me) setPop(true)
    try {
      const updated = post.liked_by_me ? await api.unlikePost(post.id) : await api.likePost(post.id)
      setPost(updated)
    } catch {
      setPost(post)
    } finally {
      setBusy(false)
      window.setTimeout(() => setPop(false), 360)
    }
  }

  async function submitComment(event: React.FormEvent) {
    event.preventDefault()
    if (!post || !draft.trim() || sending) return
    setSending(true)
    try {
      const created = await api.addComment(post.id, draft.trim())
      setComments((current) => [...current, created])
      setPost((current) => (current ? { ...current, comment_count: current.comment_count + 1 } : current))
      setDraft('')
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : 'Could not post your reply', 'error')
    } finally {
      setSending(false)
    }
  }

  // Replies are returned nested under their parent, so each update walks the
  // tree rather than touching a flat list.
  function mapTree(nodes: Comment[], fn: (node: Comment) => Comment | null): Comment[] {
    const out: Comment[] = []
    for (const node of nodes) {
      const next = fn(node)
      if (next === null) continue
      out.push({ ...next, replies: mapTree(next.replies, fn) })
    }
    return out
  }

  function countComments(nodes: Comment[]): number {
    return nodes.reduce((total, node) => total + 1 + countComments(node.replies), 0)
  }

  function handleReplied(parentId: number, reply: Comment) {
    setComments((current) =>
      mapTree(current, (node) =>
        node.id === parentId ? { ...node, replies: [...node.replies, reply] } : node,
      ),
    )
    setPost((current) => (current ? { ...current, comment_count: current.comment_count + 1 } : current))
  }

  function handleCommentDeleted(deleted: Comment) {
    // Deleting a comment also removes everything answered under it.
    const removed = 1 + countComments(deleted.replies)
    setComments((current) => mapTree(current, (node) => (node.id === deleted.id ? null : node)))
    setPost((current) =>
      current ? { ...current, comment_count: Math.max(0, current.comment_count - removed) } : current,
    )
  }

  function handleCommentLikeToggled(updated: Comment) {
    setComments((current) =>
      mapTree(current, (node) => (node.id === updated.id ? { ...node, ...updated, replies: node.replies } : node)),
    )
  }

  async function deletePost() {
    if (!post) return
    try {
      await api.deletePost(post.id)
      notify('Post deleted')
      navigate('/')
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : 'Could not delete the post', 'error')
    }
  }

  async function copyLink() {
    if (!post) return
    const url = `${window.location.origin}/p/${post.id}`
    try {
      await navigator.clipboard.writeText(url)
      notify('Link copied')
    } catch {
      window.prompt('Copy this link:', url)
    }
  }

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <button type="button" className="icon-btn" onClick={() => navigate(-1)} aria-label="Go back">
            <ArrowLeftIcon size={20} />
          </button>
          <div style={{ flex: 1 }}>
            <h1>Post</h1>
          </div>
        </div>
      </header>

      {loading && <FeedSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} onRetry={() => navigate(0)} />}

      {!loading && !error && post && (
        <>
          <article className="detail-post">
            <div className="detail-head">
              <Link to={`/u/${post.author.username}`}>
                <Avatar name={post.author.display_name} color={post.author.avatar_color} url={post.author.avatar_url} size="lg" />
              </Link>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Link to={`/u/${post.author.username}`} className="detail-name">
                  <DisplayName user={post.author} />
                </Link>
                <div className="detail-handle">@{post.author.username}</div>
              </div>
              <Dropdown trigger={() => <MoreIcon size={18} />}>
                {(close) => (
                  <>
                    <button
                      type="button"
                      className="menu-item"
                      onClick={() => {
                        close()
                        void copyLink()
                      }}
                    >
                      <ShareIcon size={16} /> Copy link
                    </button>
                    {user?.id === post.author.id && (
                      <>
                        <button
                          type="button"
                          className="menu-item"
                          onClick={() => {
                            close()
                            setEditing(true)
                          }}
                        >
                          <EditIcon size={16} /> Edit post
                        </button>
                        <button
                          type="button"
                          className="menu-item danger"
                          onClick={() => {
                            close()
                            void deletePost()
                          }}
                        >
                          <TrashIcon size={16} /> Delete post
                        </button>
                      </>
                    )}
                  </>
                )}
              </Dropdown>
            </div>

            <PostText body={post.body} className="detail-text" />

            <MediaGallery items={post.media} />

            <div className="detail-time">
              {formatFullDate(post.created_at)}
              {post.edited_at && (
                <>
                  {' · '}
                  <RelativeTime iso={post.edited_at} prefix="edited " suffix=" ago" />
                </>
              )}
            </div>

            <div className="detail-stats">
              <span>
                <strong>{formatCount(post.like_count)}</strong> likes
              </span>
              <span>
                <strong>{formatCount(post.comment_count)}</strong> replies
              </span>
            </div>
          </article>

          <div className="detail-actions">
            <button
              type="button"
              className={`action like${post.liked_by_me ? ' liked' : ''}`}
              onClick={toggleLike}
              aria-pressed={post.liked_by_me}
            >
              <span className={pop ? 'heart-pop' : undefined} style={{ display: 'grid' }}>
                <HeartIcon size={19} filled={post.liked_by_me} />
              </span>
              {post.liked_by_me ? 'Liked' : 'Like'}
            </button>
            <button
              type="button"
              className="action comment"
              onClick={() => {
                // Answering the post itself, so clear any reply target and put
                // the cursor in the main composer.
                setReplyingTo(null)
                document.getElementById('reply')?.focus()
              }}
            >
              <CommentIcon size={19} /> Reply
            </button>
            <button type="button" className="action share" onClick={copyLink}>
              <ShareIcon size={19} /> Share
            </button>
          </div>

          {user ? (
            <form className="comment-form" onSubmit={submitComment}>
              <Avatar name={user.display_name} color={user.avatar_color} url={user.avatar_url} size="sm" />
              <div className="comment-form-inner">
                <textarea
                  id="reply"
                  value={draft}
                  placeholder="Post your reply"
                  maxLength={1000}
                  onChange={(event) => setDraft(event.target.value)}
                />
                <div className="comment-form-actions">
                  <button
                    type="submit"
                    className="btn btn-primary btn-sm"
                    disabled={!draft.trim() || sending}
                  >
                    {sending ? <Spinner size={14} /> : 'Reply'}
                  </button>
                </div>
              </div>
            </form>
          ) : (
            <div className="comment-form">
              <div className="comment-form-inner" style={{ color: 'var(--text-muted)', fontSize: 14.5 }}>
                <Link to="/login" style={{ color: 'var(--accent)', fontWeight: 600 }}>
                  Sign in
                </Link>{' '}
                to join the conversation.
              </div>
            </div>
          )}

          {comments.length === 0 ? (
            <EmptyState
              icon={<CommentIcon size={24} />}
              title="No replies yet"
              description="Be the first to respond to this post."
            />
          ) : (
            comments.map((comment) => (
              <CommentNode
                key={comment.id}
                comment={comment}
                postId={post.id}
                postAuthorId={post.author.id}
                replyingTo={replyingTo?.id ?? null}
                onReplyTarget={setReplyingTo}
                onReplied={handleReplied}
                onDeleted={handleCommentDeleted}
                onLikeToggled={handleCommentLikeToggled}
              />
            ))
          )}
        </>
      )}

      {editing && post && (
        <EditPostModal
          post={post}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setPost(updated)
            setEditing(false)
          }}
        />
      )}
    </>
  )
}
