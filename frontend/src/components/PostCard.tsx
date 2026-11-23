import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Avatar, DisplayName } from './Avatar'
import {
  CommentIcon,
  EyeIcon,
  HeartIcon,
  MoreIcon,
  RepostIcon,
  ShareIcon,
  TrashIcon,
  EditIcon,
  LinkIcon,
  WarningIcon,
} from './Icons'
import { MediaGallery } from './MediaGallery'
import { ReportDialog } from './ReportDialog'
import { TranslatedText } from './TranslatedText'
import { Dropdown, RelativeTime } from './UI'
import { api } from '../lib/api'
import * as queryCache from '../lib/queryCache'
import { formatCount, segmentBody } from '../lib/format'
import type { Post } from '../lib/types'

export function PostText({ body, className }: { body: string; className?: string }) {
  return (
    <p className={className}>
      {segmentBody(body).map((segment, index) => {
        if (segment.type === 'tag') {
          return (
            <Link key={index} className="hashtag" to={`/search?q=${encodeURIComponent('#' + segment.value)}`}>
              #{segment.value}
            </Link>
          )
        }
        if (segment.type === 'mention') {
          return (
            <Link key={index} className="mention" to={`/u/${segment.value}`}>
              @{segment.value}
            </Link>
          )
        }
        return <span key={index}>{segment.value}</span>
      })}
    </p>
  )
}

interface PostCardProps {
  post: Post
  currentUserId?: number
  onChange?: (post: Post) => void
  onDeleted?: (id: number) => void
  onEdit?: (post: Post) => void
  clickable?: boolean
  /**
   * Render for a visitor with no session: the content is identical, but the
   * interactions send them to sign in instead of firing a request that would
   * come back 401. Used by the public profile page.
   */
  readOnly?: boolean
}

export function PostCard({
  post,
  currentUserId,
  onChange,
  onDeleted,
  onEdit,
  clickable = true,
  readOnly = false,
}: PostCardProps) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [busy, setBusy] = useState(false)
  const [pop, setPop] = useState(false)
  const [reporting, setReporting] = useState(false)

  // A repost row is an envelope: it carries who shared and when, while the
  // content, author and every interaction belong to the original it wraps. So
  // the whole card renders `content` and only the header line reads `post`.
  const content = post.repost_of ?? post
  const isRepost = post.repost_of !== null
  const isOwn = currentUserId === content.author.id
  const isOwnPost = currentUserId === post.author.id

  /** Send a signed-out visitor to the login screen, remembering where they were. */
  function requireSession(event?: React.MouseEvent) {
    event?.stopPropagation()
    navigate('/login', { state: { from: pathname } })
  }

  async function toggleLike(event: React.MouseEvent) {
    event.stopPropagation()
    if (readOnly) return requireSession(event)
    if (busy) return
    setBusy(true)
    // Optimistic update so the tap feels instant.
    const optimistic: Post = {
      ...content,
      liked_by_me: !content.liked_by_me,
      like_count: content.like_count + (content.liked_by_me ? -1 : 1),
    }
    onChange?.(optimistic)
    if (!content.liked_by_me) setPop(true)
    try {
      const updated = content.liked_by_me
        ? await api.unlikePost(content.id)
        : await api.likePost(content.id)
      onChange?.(updated)
    } catch {
      onChange?.(content)
    } finally {
      setBusy(false)
      window.setTimeout(() => setPop(false), 360)
    }
  }

  async function toggleRepost(event?: React.MouseEvent) {
    event?.stopPropagation()
    if (readOnly) return requireSession(event)
    if (busy) return
    setBusy(true)
    // Optimistic, mirroring the like button.
    const optimistic: Post = {
      ...content,
      reposted_by_me: !content.reposted_by_me,
      repost_count: content.repost_count + (content.reposted_by_me ? -1 : 1),
    }
    onChange?.(optimistic)
    try {
      const updated = content.reposted_by_me
        ? await api.unrepostPost(content.id)
        : await api.repostPost(content.id)
      onChange?.(updated)
    } catch {
      onChange?.(content)
    } finally {
      setBusy(false)
    }
  }

  const postUrl = `${window.location.origin}/p/${content.id}`

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(postUrl)
    } catch {
      window.prompt('Copy this link:', postUrl)
    }
  }

  /**
   * Share through the OS sheet when the browser has one — on a phone that is
   * the whole point of the button — and fall back to the link otherwise.
   */
  async function share(event: React.MouseEvent) {
    event.stopPropagation()
    const data = { title: content.author.display_name, text: content.body.slice(0, 140), url: postUrl }
    if (navigator.share) {
      try {
        await navigator.share(data)
        return
      } catch {
        // The reader dismissed the sheet, or the share failed; the link is
        // still useful, so fall through to copying it.
      }
    }
    await copyLink()
  }

  async function remove() {
    await api.deletePost(post.id)
    // A delete lowers the author's post count and removes the post from their
    // profile feed, both of which are cached.
    queryCache.invalidate('user:')
    queryCache.invalidate('profile:')
    onDeleted?.(post.id)
  }

  const openDetail = () => {
    if (clickable) navigate(`/p/${content.id}`)
  }

  return (
    <article
      className={`post${clickable ? ' post-clickable' : ''}${isRepost ? ' post-repost' : ''}`}
      onClick={openDetail}
      onKeyDown={(event) => {
        if (clickable && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          openDetail()
        }
      }}
      tabIndex={clickable ? 0 : undefined}
    >
      {isRepost && (
        <div className="repost-flag">
          <RepostIcon size={15} />
          <Link
            to={`/u/${post.author.username}`}
            className="repost-flag-actor"
            onClick={(event) => event.stopPropagation()}
          >
            {post.author.display_name}
          </Link>{' '}
          reposted
        </div>
      )}

      <div className="post-row">
        <Link to={`/u/${content.author.username}`} onClick={(event) => event.stopPropagation()}>
          <Avatar
            name={content.author.display_name}
            color={content.author.avatar_color}
            url={content.author.avatar_url}
            size="md"
          />
        </Link>

        <div className="post-body-col">
          <div className="post-head">
            <Link
              className="post-author"
              to={`/u/${content.author.username}`}
              onClick={(e) => e.stopPropagation()}
            >
              <DisplayName user={content.author} />
            </Link>
            <span className="post-handle">@{content.author.username}</span>
            <span className="post-dot">·</span>
            <RelativeTime iso={content.created_at} className="post-time" />
            {content.edited_at && <span className="post-time">(edited)</span>}

            <div style={{ marginLeft: 'auto' }} onClick={(event) => event.stopPropagation()}>
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
                    {/* Reporting someone else's content. Your own post is
                        offered edit and delete instead, because complaining
                        about yourself is not a moderation case. */}
                    {!isOwnPost && !readOnly && (
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
                    {/* Editing and deleting act on the row itself, so a repost
                        can be removed but not rewritten. */}
                    {isOwnPost && (
                      <>
                        {!isRepost && (
                          <button
                            type="button"
                            className="menu-item"
                            onClick={() => {
                              close()
                              onEdit?.(content)
                            }}
                          >
                            <EditIcon size={16} /> Edit post
                          </button>
                        )}
                        <button
                          type="button"
                          className="menu-item danger"
                          onClick={() => {
                            close()
                            void remove()
                          }}
                        >
                          <TrashIcon size={16} /> {isRepost ? 'Undo repost' : 'Delete post'}
                        </button>
                      </>
                    )}
                  </>
                )}
              </Dropdown>
            </div>
          </div>

          {content.body && <TranslatedText post={content} className="post-text" />}

          <MediaGallery items={content.media} />

          {/* One bar, two groups: what a reader can do on the left, how far
              the post has travelled on the right. Keeping the reach readout
              apart is what stops it reading as a fourth button. */}
          <div className="post-actions">
            <div className="action-group">
              <button
                type="button"
                className="action comment"
                onClick={(event) => {
                  event.stopPropagation()
                  navigate(`/p/${content.id}`)
                }}
                aria-label={
                  content.comment_count > 0
                    ? `View ${content.comment_count} comments`
                    : 'View comments'
                }
              >
                <CommentIcon size={17} />
                {content.comment_count > 0 && <span>{formatCount(content.comment_count)}</span>}
              </button>

              <button
                type="button"
                className={`action like${content.liked_by_me ? ' liked' : ''}`}
                onClick={toggleLike}
                aria-pressed={content.liked_by_me}
                aria-label={content.liked_by_me ? 'Unlike' : 'Like'}
              >
                <span className={pop ? 'heart-pop' : undefined} style={{ display: 'grid' }}>
                  <HeartIcon size={17} filled={content.liked_by_me} />
                </span>
                {content.like_count > 0 && <span>{formatCount(content.like_count)}</span>}
              </button>

              {/* Reposting your own post is rejected by the API, so the button
                  is omitted rather than offered and then refused. */}
              {!isOwn && (
                <button
                  type="button"
                  className={`action repost${content.reposted_by_me ? ' reposted' : ''}`}
                  onClick={toggleRepost}
                  aria-pressed={content.reposted_by_me}
                  aria-label={content.reposted_by_me ? 'Undo repost' : 'Repost'}
                  title={content.reposted_by_me ? 'Undo repost' : 'Repost'}
                >
                  <RepostIcon size={17} active={content.reposted_by_me} />
                  {content.repost_count > 0 && <span>{formatCount(content.repost_count)}</span>}
                </button>
              )}

              <button
                type="button"
                className="action share"
                onClick={share}
                aria-label="Share post"
                title="Share"
              >
                <ShareIcon size={17} />
              </button>
            </div>

            {/* Reach. It reads at zero so a fresh post shows a number rather
                than a gap, and it is not a control, so it never lights up. */}
            <span className="action views" aria-label={`${content.view_count} views`}>
              <EyeIcon size={16} />
              <span>{formatCount(content.view_count)}</span>
            </span>
          </div>
        </div>
      </div>

      {reporting && <ReportDialog postId={content.id} onClose={() => setReporting(false)} />}
    </article>
  )
}
