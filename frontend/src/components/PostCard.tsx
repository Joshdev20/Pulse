import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Avatar, DisplayName } from './Avatar'
import { CommentIcon, HeartIcon, MoreIcon, ShareIcon, TrashIcon, EditIcon } from './Icons'
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

  const isOwn = currentUserId === post.author.id

  async function toggleLike(event: React.MouseEvent) {
    event.stopPropagation()
    if (busy) return
    setBusy(true)
    // Optimistic update so the tap feels instant.
    const optimistic: Post = {
      ...post,
      liked_by_me: !post.liked_by_me,
      like_count: post.like_count + (post.liked_by_me ? -1 : 1),
    }
    onChange?.(optimistic)
    if (!post.liked_by_me) setPop(true)
    try {
      const updated = post.liked_by_me ? await api.unlikePost(post.id) : await api.likePost(post.id)
      onChange?.(updated)
    } catch {
      onChange?.(post)
    } finally {
      setBusy(false)
      window.setTimeout(() => setPop(false), 360)
    }
  }

  const copyLink = async () => {
    const url = `${window.location.origin}/p/${post.id}`
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
    if (clickable) navigate(`/p/${post.id}`)
  }

  return (
    <article
      className={`post${clickable ? ' post-clickable' : ''}`}
      onClick={openDetail}
      onKeyDown={(event) => {
        if (clickable && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          openDetail()
        }
      }}
      tabIndex={clickable ? 0 : undefined}
    >
      <Link to={`/u/${post.author.username}`} onClick={(event) => event.stopPropagation()}>
        <Avatar name={post.author.display_name} color={post.author.avatar_color} url={post.author.avatar_url} size="md" />
      </Link>

      <div className="post-body-col">
        <div className="post-head">
          <Link className="post-author" to={`/u/${post.author.username}`} onClick={(e) => e.stopPropagation()}>
            <DisplayName user={post.author} />
          </Link>
          <span className="post-handle">@{post.author.username}</span>
          <span className="post-dot">·</span>
          <RelativeTime iso={post.created_at} className="post-time" />
          {post.edited_at && <span className="post-time">(edited)</span>}

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
                  {isOwn && (
                    <>
                      <button
                        type="button"
                        className="menu-item"
                        onClick={() => {
                          close()
                          onEdit?.(post)
                        }}
                      >
                        <EditIcon size={16} /> Edit post
                      </button>
                      <button
                        type="button"
                        className="menu-item danger"
                        onClick={() => {
                          close()
                          void remove()
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
        </div>

        {post.body && <PostText body={post.body} className="post-text" />}

        <MediaGallery items={post.media} />

        <div className="post-actions">
          <button
            type="button"
            className={`action comment`}
            onClick={(event) => {
              event.stopPropagation()
              navigate(`/p/${post.id}`)
            }}
            aria-label="View comments"
          >
            <CommentIcon size={17} />
            {post.comment_count > 0 && <span>{formatCount(post.comment_count)}</span>}
          </button>

          <button
            type="button"
            className={`action like${post.liked_by_me ? ' liked' : ''}`}
            onClick={toggleLike}
            aria-pressed={post.liked_by_me}
            aria-label={post.liked_by_me ? 'Unlike' : 'Like'}
          >
            <span className={pop ? 'heart-pop' : undefined} style={{ display: 'grid' }}>
              <HeartIcon size={17} filled={post.liked_by_me} />
            </span>
            {post.like_count > 0 && <span>{formatCount(post.like_count)}</span>}
          </button>

          <button type="button" className="action share" onClick={share} aria-label="Copy link">
            <ShareIcon size={17} />
          </button>
        </div>
      </div>
    </article>
  )
}
