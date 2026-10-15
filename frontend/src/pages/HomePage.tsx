import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Composer } from '../components/Composer'
import { PostCard } from '../components/PostCard'
import { EmptyState, ErrorState, FeedSkeleton, Spinner } from '../components/UI'
import { UsersIcon, SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { pluralize } from '../lib/format'
import { usePostFeed } from '../lib/usePostFeed'
import type { Post } from '../lib/types'
import { EditPostModal } from '../components/EditPostModal'

export function HomePage() {
  const { user, loading: authLoading } = useAuth()
  const [scope, setScope] = useState<'following' | 'all'>('following')
  const [editing, setEditing] = useState<Post | null>(null)

  // Signed-out visitors browse the public timeline.
  const effectiveScope = user ? scope : 'all'
  const feed = usePostFeed(
    (page) => api.feed(effectiveScope, page, 20),
    [effectiveScope, user?.id],
  )

  if (authLoading) {
    return (
      <div className="state">
        <Spinner size={22} />
      </div>
    )
  }

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <div style={{ flex: 1 }}>
            <h1>Home</h1>
            <div className="sub">{user ? `Welcome back, ${user.display_name}` : 'The public timeline'}</div>
          </div>
          {user && (
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
          )}
        </div>
      </header>

      {user && <Composer onPosted={feed.prepend} />}

      {!user && (
        <div className="card" style={{ margin: 16, padding: 18 }}>
          <h2 style={{ fontSize: 19 }}>Join the conversation</h2>
          <p style={{ color: 'var(--text-muted)', marginTop: 6, fontSize: 14.5 }}>
            Create an account to post, follow people and build your own timeline.
          </p>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <Link to="/register" className="btn btn-primary">
              Create account
            </Link>
            <Link to="/login" className="btn btn-outline">
              Sign in
            </Link>
          </div>
        </div>
      )}

      <div>
        {feed.loading && <FeedSkeleton rows={5} />}

        {!feed.loading && feed.error && <ErrorState message={feed.error} onRetry={feed.reload} />}

        {!feed.loading && !feed.error && feed.posts.length === 0 && (
          <EmptyState
            icon={<UsersIcon size={24} />}
            title={effectiveScope === 'following' ? 'Your timeline is quiet' : 'No posts yet'}
            description={
              effectiveScope === 'following'
                ? 'Follow a few people, or switch to Everyone to see what the whole community is posting.'
                : 'Be the first to share something with the community.'
            }
            action={
              effectiveScope === 'following' ? (
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setScope('all')}>
                  Browse everyone
                </button>
              ) : (
                <Link to="/explore" className="btn btn-secondary btn-sm">
                  <SparkIcon size={16} /> Explore
                </Link>
              )
            }
          />
        )}

        {!feed.loading &&
          feed.posts.map((post) => (
            <PostCard
              key={post.id}
              post={post}
              currentUserId={user?.id}
              onChange={feed.replace}
              onDeleted={feed.remove}
              onEdit={setEditing}
            />
          ))}

        {!feed.loading && feed.hasMore && (
          <div className="load-more">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={feed.loadingMore}
              onClick={feed.loadMore}
            >
              {feed.loadingMore ? <Spinner size={16} /> : 'Load more'}
            </button>
          </div>
        )}

        {!feed.loading && !feed.hasMore && feed.posts.length > 0 && (
          <div className="load-more">
            <span className="card-sub">{pluralize(feed.total, 'post')} in this timeline</span>
          </div>
        )}
      </div>

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
