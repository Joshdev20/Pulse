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
 * The seed for one feed ordering.
 *
 * The server shuffles by seed, so a new seed is a new arrangement of the same
 * candidate posts. It is kept for the life of the tab rather than drawn fresh
 * on every mount: the server caches a feed page by (viewer, page, scope, seed),
 * so a new seed on every launch guaranteed a cache miss and made every
 * installed-PWA start pay for a full re-rank. A returning visitor still sees a
 * different order than another account, because the server mixes the viewer id
 * into the shuffle.
 */
const FEED_SEED_KEY = 'pulse.feedSeed'

function feedSeed(): string {
  try {
    const saved = sessionStorage.getItem(FEED_SEED_KEY)
    if (saved) return saved
  } catch {
    // Private mode can throw on access; fall through to a fresh seed.
  }
  const seed = Math.random().toString(36).slice(2, 10)
  try {
    sessionStorage.setItem(FEED_SEED_KEY, seed)
  } catch {
    // Not persisting is harmless: the seed still holds for this mount.
  }
  return seed
}

export function HomePage() {
  const { user } = useAuth()
  // Everyone is the default view: a new account with no follows would otherwise
  // land on an empty timeline. Following stays one tap away.
  const [scope, setScope] = useState<'following' | 'all'>('all')
  const [editing, setEditing] = useState<Post | null>(null)

  // One seed per session, so "load more" keeps the same shuffled order while
  // switching tabs reshuffles. Re-selecting the Everyone tab draws a new seed,
  // which is how a reader asks for a fresh arrangement.
  const [seed, setSeed] = useState(() => feedSeed())

  const feed = usePostFeed(
    (page) => api.feed(scope, page, 20, seed),
    [scope, user?.id, seed],
  )

  const chooseScope = (next: 'following' | 'all') => {
    setScope(next)
    const fresh = Math.random().toString(36).slice(2, 10)
    setSeed(fresh)
    try {
      sessionStorage.setItem(FEED_SEED_KEY, fresh)
    } catch {
      // See above: persistence is an optimisation, not a requirement.
    }
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
