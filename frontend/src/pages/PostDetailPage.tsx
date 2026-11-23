import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar, DisplayName } from '../components/Avatar'
import { MediaGallery } from '../components/MediaGallery'
import { CommentNode } from '../components/CommentThread'
import { EditPostModal } from '../components/EditPostModal'
import { ReportDialog } from '../components/ReportDialog'
import { EmptyState, ErrorState, FeedSkeleton, Spinner, Dropdown, RelativeTime } from '../components/UI'
import {
  ArrowLeftIcon,
  CloseIcon,
  CommentIcon,
  EyeIcon,
  HeartIcon,
  LinkIcon,
  MoreIcon,
  RepostIcon,
  ShareIcon,
  TrashIcon,
  EditIcon,
  WarningIcon,
} from '../components/Icons'
import { TranslatedText } from '../components/TranslatedText'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'
import * as queryCache from '../lib/queryCache'
import { formatCount, formatFullDate, pluralize } from '../lib/format'
import { usePullToRefresh } from '../lib/usePullToRefresh'
import type { Comment, Post } from '../lib/types'

/**
 * A post on its own, with its replies.
 *
 * This is the view a reader lands on after tapping a post, so it is built
 * around the content rather than around the feed's compact row: the media is
 * full width, the counts are spelled out, and every way out is one tap — the
 * back arrow, a close button, Escape, or the browser's own back.
 */
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
  const [reporting, setReporting] = useState(false)
  const [replyingTo, setReplyingTo] = useState<Comment | null>(null)

  useEffect(() => {
    if (!Number.isFinite(id)) {
      setError('That post link is not valid')
      setLoading(false)
      return
    }
    let active = true

    // Paint what was read last time, then refresh behind it. Reopening a post
    // is the common case after a back-navigation, and it should not cost a
    // round trip before anything appears.
    const cachedPost = queryCache.read<Post>(`post:${id}`)
    const cachedComments = queryCache.read<Comment[]>(`comments:${id}`)
    if (cachedPost) setPost(cachedPost)
    if (cachedComments) setComments(cachedComments)
    setLoading(!cachedPost)
    setError(null)

    Promise.all([
      queryCache.load(`post:${id}`, () => api.getPost(id)),
      queryCache.load(`comments:${id}`, () => api.comments(id)),
    ])
      .then(([postData, commentData]) => {
        if (!active) return
        setPost(postData)
        setComments(commentData)
      })
      .catch((caught) => {
        // A refresh failure leaves the cached post on screen; only a cold open
        // has nothing to show.
        if (active && !cachedPost) {
          setError(caught instanceof Error ? caught.message : 'Could not load this post')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [id])

  // Pulling down re-reads the post and its replies, skipping the cached copy:
  // the reader asked for the current counts, not what was there a minute ago.
  usePullToRefresh(async () => {
    if (!Number.isFinite(id)) return
    const [postData, commentData] = await Promise.all([
      queryCache.load(`post:${id}`, () => api.getPost(id), undefined, true),
      queryCache.load(`comments:${id}`, () => api.comments(id), undefined, true),
    ])
    setPost(postData)
    setComments(commentData)
  })

  // Escape leaves the post, but only when nothing is layered over it — the
  // media viewer and the report dialog own the key while they are open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (document.querySelector('.viewer, .modal-backdrop')) return
      navigate(-1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [navigate])

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
      // Feed copies of this post carry the like count too, so both caches go.
      queryCache.invalidate('post:')
      queryCache.invalidate('feed:')
    } catch {
      setPost(post)
    } finally {
      setBusy(false)
      window.setTimeout(() => setPop(false), 360)
    }
  }

  async function toggleRepost() {
    if (!post) return
    if (!user) {
      navigate('/login')
      return
    }
    if (busy) return
    setBusy(true)
    setPost({
      ...post,
      reposted_by_me: !post.reposted_by_me,
      repost_count: post.repost_count + (post.reposted_by_me ? -1 : 1),
    })
    try {
      const updated = post.reposted_by_me
        ? await api.unrepostPost(post.id)
        : await api.repostPost(post.id)
      setPost(updated)
      queryCache.invalidate('post:')
      queryCache.invalidate('feed:')
    } catch {
      setPost(post)
    } finally {
      setBusy(false)
    }
  }

  async function submitComment(event: React.FormEvent) {
    event.preventDefault()
    if (!post || !draft.trim() || sending) return
    setSending(true)
    try {
      const created = await api.addComment(post.id, draft.trim())
      queryCache.invalidate(`comments:${post.id}`)
      queryCache.invalidate(`post:${post.id}`)
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
      queryCache.invalidate(`post:${post.id}`)
      queryCache.invalidate(`comments:${post.id}`)
      queryCache.invalidate('feed:')
      queryCache.invalidate('user:')
      queryCache.invalidate('profile:')
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

  async function share() {
    if (!post) return
    const url = `${window.location.origin}/p/${post.id}`
    if (navigator.share) {
      try {
        await navigator.share({
          title: post.author.display_name,
          text: post.body.slice(0, 140),
          url,
        })
        return
      } catch {
        // Dismissed or unsupported; copying the link is the useful fallback.
      }
    }
    await copyLink()
  }

  const isOwnPost = user?.id === post?.author.id

  return (
    <>
      <header className="page-head focus-bar">
        <div className="page-head-inner">
          <button type="button" className="icon-btn" onClick={() => navigate(-1)} aria-label="Go back">
            <ArrowLeftIcon size={20} />
          </button>
          <div style={{ flex: 1 }}>
            <h1>Post</h1>
          </div>
          <button
            type="button"
            className="icon-btn focus-close"
            onClick={() => navigate('/')}
            aria-label="Close post"
          >
            <CloseIcon size={20} />
          </button>
        </div>
      </header>

      {loading && <FeedSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} onRetry={() => navigate(0)} />}

      {!loading && !error && post && (
        <>
          <article className="focus-post">
            <div className="focus-author">
              <Link to={`/u/${post.author.username}`} className="focus-avatar">
                <Avatar
                  name={post.author.display_name}
                  color={post.author.avatar_color}
                  url={post.author.avatar_url}
                  size="lg"
                />
              </Link>
              <div className="focus-author-text">
                <Link to={`/u/${post.author.username}`} className="focus-name">
                  <DisplayName user={post.author} />
                </Link>
                <div className="focus-handle">
                  @{post.author.username}
                  <span className="focus-sep">·</span>
                  <RelativeTime iso={post.created_at} />
                  {post.edited_at && <span className="focus-edited">edited</span>}
                </div>
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
                      <LinkIcon size={16} /> Copy link
                    </button>
                    {isOwnPost ? (
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
                    ) : (
                      <button
                        type="button"
                        className="menu-item danger"
                        onClick={() => {
                          close()
                          setReporting(true)
                        }}
                      >
                        <WarningIcon size={16} /> Report post
                      </button>
                    )}
                  </>
                )}
              </Dropdown>
            </div>

            {post.body && <TranslatedText post={post} className="focus-text" />}

            <MediaGallery items={post.media} variant="focus" />

            <div className="focus-time">{formatFullDate(post.created_at)}</div>

            <div className="focus-stats">
              <span className="focus-stat">
                <EyeIcon size={15} />
                <strong>{formatCount(post.view_count)}</strong> {pluralize(post.view_count, 'view')}
              </span>
              <span className="focus-stat">
                <RepostIcon size={15} />
                <strong>{formatCount(post.repost_count)}</strong> {pluralize(post.repost_count, 'repost')}
              </span>
              <span className="focus-stat">
                <HeartIcon size={15} />
                <strong>{formatCount(post.like_count)}</strong> {pluralize(post.like_count, 'like')}
              </span>
              <span className="focus-stat">
                <CommentIcon size={15} />
                <strong>{formatCount(post.comment_count)}</strong> {pluralize(post.comment_count, 'reply')}
              </span>
            </div>

            <div className="focus-actions">
              <button
                type="button"
                className="focus-action comment"
                onClick={() => {
                  setReplyingTo(null)
                  document.getElementById('reply')?.focus()
                }}
              >
                <CommentIcon size={19} />
                <span>Reply</span>
              </button>
              <button
                type="button"
                className={`focus-action repost${post.reposted_by_me ? ' on' : ''}`}
                onClick={toggleRepost}
                aria-pressed={post.reposted_by_me}
              >
                <RepostIcon size={19} active={post.reposted_by_me} />
                <span>{post.reposted_by_me ? 'Reposted' : 'Repost'}</span>
              </button>
              <button
                type="button"
                className={`focus-action like${post.liked_by_me ? ' on' : ''}`}
                onClick={toggleLike}
                aria-pressed={post.liked_by_me}
              >
                <span className={pop ? 'heart-pop' : undefined} style={{ display: 'grid' }}>
                  <HeartIcon size={19} filled={post.liked_by_me} />
                </span>
                <span>{post.liked_by_me ? 'Liked' : 'Like'}</span>
              </button>
              <button type="button" className="focus-action share" onClick={share}>
                <ShareIcon size={19} />
                <span>Share</span>
              </button>
            </div>
          </article>

          <section className="focus-replies" aria-label="Replies">
            <h2 className="focus-replies-head">
              Replies <span>{formatCount(post.comment_count)}</span>
            </h2>

            {user ? (
              <form className="comment-form focus-comment-form" onSubmit={submitComment}>
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
          </section>
        </>
      )}

      {editing && post && (
        <EditPostModal
          post={post}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setPost(updated)
            queryCache.write(`post:${updated.id}`, updated)
            queryCache.invalidate('feed:')
            setEditing(false)
          }}
        />
      )}

      {reporting && post && <ReportDialog postId={post.id} onClose={() => setReporting(false)} />}
    </>
  )
}
