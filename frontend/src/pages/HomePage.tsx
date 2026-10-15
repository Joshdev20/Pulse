import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Composer } from '../components/Composer'
import { InfiniteFeed } from '../components/InfiniteFeed'
import { PostCard } from '../components/PostCard'
import { EmptyState, ErrorState, FeedSkeleton } from '../components/UI'
import { UsersIcon, SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { usePostFeed } from '../lib/usePostFeed'
import type { Post } from '../lib/types'
import { EditPostModal } from '../components/EditPostModal'

export function HomePage() {
  const { user } = useAuth()
  const [scope, setScope] = useState<'following' | 'all'>('following')
  const [editing, setEditing] = useState<Post | null>(null)

  const feed = usePostFeed((page) => api.feed(scope, page, 20), [scope, user?.id])

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <div style={{ flex: 1 }}>
            <h1>Home</h1>
            <div className="sub">Welcome back, {user?.display_name}</div>
          </div>
          <div className="segmented">
            <button
              type="button"
              className={`segment${scope === 'following' ? ' active' : ''}`}
              onClick={() => setScope('following')}
            >
              Following
            </button>
            <button
              type="button"
              className={`segment${scope === 'all' ? ' active' : ''}`}
              onClick={() => setScope('all')}
            >
              Everyone
            </button>
          </div>
        </div>
      </header>

      <Composer onPosted={feed.prepend} />

      {feed.loading && <FeedSkeleton rows={5} />}

      {!feed.loading && feed.error && <ErrorState message={feed.error} onRetry={feed.reload} />}

      {!feed.loading && !feed.error && (
        <InfiniteFeed
          hasMore={feed.hasMore}
          loading={feed.loadingMore}
          onLoadMore={feed.loadMore}
          emptyState={
            <EmptyState
              icon={<UsersIcon size={24} />}
              title={scope === 'following' ? 'Your timeline is quiet' : 'No posts yet'}
              description={
                scope === 'following'
                  ? 'Follow a few people, or switch to Everyone to see what the whole community is posting.'
                  : 'Be the first to share something with the community.'
              }
              action={
                scope === 'following' ? (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setScope('all')}
                  >
                    Browse everyone
                  </button>
                ) : (
                  <Link to="/explore" className="btn btn-secondary btn-sm">
                    <SparkIcon size={16} /> Explore
                  </Link>
                )
              }
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
