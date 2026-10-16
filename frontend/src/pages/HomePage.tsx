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

/**
 * A fresh seed for one feed ordering.
 *
 * The server shuffles by seed, so a new seed is a new arrangement of the same
 * candidate posts. It is short and random rather than derived from the clock so
 * two users loading at the same moment still see different orders.
 */
function randomSeed(): string {
  return Math.random().toString(36).slice(2, 10)
}

export function HomePage() {
  const { user } = useAuth()
  // Everyone is the default view: a new account with no follows would otherwise
  // land on an empty timeline. Following stays one tap away.
  const [scope, setScope] = useState<'following' | 'all'>('all')
  const [editing, setEditing] = useState<Post | null>(null)

  // One seed per scope change, so "load more" keeps the same shuffled order
  // while switching tabs reshuffles. Regenerated when the tab is re-selected
  // via the button below, so a manual refresh of Everyone gives fresh posts.
  const [feedSeed, setFeedSeed] = useState(() => randomSeed())

  const feed = usePostFeed(
    (page) => api.feed(scope, page, 20, feedSeed),
    [scope, user?.id, feedSeed],
  )

  const chooseScope = (next: 'following' | 'all') => {
    setScope(next)
    setFeedSeed(randomSeed())
  }

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
              onClick={() => chooseScope('following')}
            >
              Following
            </button>
            <button
              type="button"
              className={`segment${scope === 'all' ? ' active' : ''}`}
              onClick={() => chooseScope('all')}
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
                  ? 'Follow a few people to fill this timeline, or switch to Everyone to see what the whole community is posting.'
                  : 'Be the first to share something with the community.'
              }
              action={
                scope === 'following' ? (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => chooseScope('all')}
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
