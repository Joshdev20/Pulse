import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PostCard } from '../components/PostCard'
import { Avatar, DisplayName } from '../components/Avatar'
import { FollowButton } from '../components/FollowButton'
import { EditPostModal } from '../components/EditPostModal'
import { EmptyState, ErrorState, FeedSkeleton } from '../components/UI'
import { InfiniteFeed } from '../components/InfiniteFeed'
import { SearchIcon, SparkIcon, TrendIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { usePostFeed } from '../lib/usePostFeed'
import { formatCount } from '../lib/format'
import type { Post } from '../lib/types'

export function ExplorePage() {
  const { user } = useAuth()
  const [editing, setEditing] = useState<Post | null>(null)
  const [tab, setTab] = useState<'posts' | 'people' | 'tags'>('posts')

  const feed = usePostFeed((page) => api.posts(page, 20), [])
  const suggestions = useAsync(() => api.suggested(8), [user?.id])
  const tags = useAsync(() => api.trendingTags(20), [])

  const suggested = suggestions.data ?? []

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <div style={{ flex: 1 }}>
            <h1>Explore</h1>
            <div className="sub">Discover posts, people and topics</div>
          </div>
          <Link to="/search" className="icon-btn" aria-label="Search">
            <SearchIcon size={20} />
          </Link>
        </div>
        <div className="tabs">
          <button type="button" className={`tab${tab === 'posts' ? ' active' : ''}`} onClick={() => setTab('posts')}>
            Latest
          </button>
          <button type="button" className={`tab${tab === 'people' ? ' active' : ''}`} onClick={() => setTab('people')}>
            People
          </button>
          <button type="button" className={`tab${tab === 'tags' ? ' active' : ''}`} onClick={() => setTab('tags')}>
            Topics
          </button>
        </div>
      </header>

      {tab === 'posts' && (
        <div>
          {feed.loading && <FeedSkeleton rows={5} />}
          {!feed.loading && feed.error && <ErrorState message={feed.error} onRetry={feed.reload} />}
          {!feed.loading && !feed.error && (
            <InfiniteFeed
              hasMore={feed.hasMore}
              loading={feed.loadingMore}
              onLoadMore={feed.loadMore}
              emptyState={
                <EmptyState
                  icon={<SparkIcon size={24} />}
                  title="Nothing here yet"
                  description="Once people start posting, their updates will show up here."
                />
              }
            >
              {feed.posts.map((post) => (
                <PostCard
                  key={post.id}
                  post={post}
                  currentUserId={user?.id}
                  onChange={feed.replace}
                  onDeleted={feed.remove}
                  onEdit={setEditing}
                />
              ))}
            </InfiniteFeed>
          )}
        </div>
      )}

      {tab === 'people' && (
        <div>
          {suggestions.loading && <FeedSkeleton rows={4} />}
          {!suggestions.loading && suggested.length === 0 && (
            <EmptyState
              icon={<SparkIcon size={24} />}
              title="No suggestions"
              description="You are already following everyone we could find."
            />
          )}
          {suggested.map((item) => (
            <div key={item.user.id} className="user-row">
              <Link to={`/u/${item.user.username}`}>
                <Avatar name={item.user.display_name} color={item.user.avatar_color} url={item.user.avatar_url} size="md" />
              </Link>
              <div className="user-row-main">
                <div className="user-row-name">
                  <Link to={`/u/${item.user.username}`}>
                    <DisplayName user={item.user} />
                  </Link>
                </div>
                <div className="user-row-handle">@{item.user.username}</div>
                {item.user.bio && <p className="user-row-bio">{item.user.bio}</p>}
                <div className="card-sub" style={{ marginTop: 4 }}>
                  {item.reason}
                </div>
              </div>
              <div className="user-row-actions">
                <FollowButton
                  user={item.user}
                  size="sm"
                  onChange={(updated) =>
                    suggestions.setData(
                      (current) =>
                        current?.map((entry) =>
                          entry.user.id === updated.id ? { ...entry, user: updated } : entry,
                        ) ?? null,
                    )
                  }
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'tags' && (
        <div style={{ padding: 18 }}>
          {tags.loading && <FeedSkeleton rows={3} />}
          {!tags.loading && (tags.data ?? []).length === 0 && (
            <EmptyState
              icon={<TrendIcon size={24} />}
              title="No topics yet"
              description="Hashtags appear here once people start using them in posts."
            />
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
            {(tags.data ?? []).map((tag) => (
              <Link key={tag.name} className="tag-chip" to={`/search?q=${encodeURIComponent('#' + tag.name)}`}>
                #{tag.name}
                <span className="uses">{formatCount(tag.uses)}</span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {editing && (
        <EditPostModal
          post={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            feed.replace(updated)
            setEditing(null)
          }}
        />
      )}
    </>
  )
}
