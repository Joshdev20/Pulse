import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Avatar, DisplayName } from './Avatar'
import { CommentIcon, HeartIcon, MoreIcon, RepostIcon, ShareIcon, TrashIcon, EditIcon } from './Icons'
import { MediaGallery } from './MediaGallery'
import { Dropdown, RelativeTime } from './UI'
import { api } from '../lib/api'
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
}

export function PostCard({
  post,
  currentUserId,
  onChange,
  onDeleted,
  onEdit,
  clickable = true,
}: PostCardProps) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [pop, setPop] = useState(false)

  // A repost row is an envelope: it carries who shared and when, while the
  // content, author and every interaction belong to the original it wraps. So
  // the whole card renders `content` and only the header line reads `post`.
  const content = post.repost_of ?? post
  const isRepost = post.repost_of !== null
  const isOwn = currentUserId === content.author.id
  const isOwnPost = currentUserId === post.author.id

  async function toggleLike(event: React.MouseEvent) {
    event.stopPropagation()
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

  const copyLink = async () => {
    const url = `${window.location.origin}/p/${content.id}`
    try {
      await navigator.clipboard.writeText(url)
    } catch {
      window.prompt('Copy this link:', url)
    }
  }

  const share = (event: React.MouseEvent) => {
    event.stopPropagation()
    void copyLink()
  }

  async function remove() {
    await api.deletePost(post.id)
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
                      <ShareIcon size={16} /> Copy link
                    </button>
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

          {content.body && <PostText body={content.body} className="post-text" />}

          <MediaGallery items={content.media} />

          <div className="post-actions">
            <button
              type="button"
              className="action comment"
              onClick={(event) => {
                event.stopPropagation()
                navigate(`/p/${content.id}`)
              }}
              aria-label="View comments"
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

            {/* Reposting your own post is rejected by the API, so the button is
                omitted rather than offered and then refused. */}
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

            <button type="button" className="action share" onClick={share} aria-label="Copy link">
              <ShareIcon size={17} />
            </button>
          </div>
        </div>
      </div>
    </article>
  )
}
