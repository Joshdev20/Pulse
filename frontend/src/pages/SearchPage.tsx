import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PostCard } from '../components/PostCard'
import { UserRow } from '../components/UserRow'
import { EditPostModal } from '../components/EditPostModal'
import { EmptyState, ErrorState, FeedSkeleton } from '../components/UI'
import { CloseIcon, SearchIcon, SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { Post, User } from '../lib/types'

export function SearchPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const initial = params.get('q') ?? ''
  const [query, setQuery] = useState(initial)
  const [debounced, setDebounced] = useState(initial.trim())
  const [tab, setTab] = useState<'posts' | 'people'>('posts')
  const [editing, setEditing] = useState<Post | null>(null)

  // Keep the input in sync when the URL changes (e.g. clicking a hashtag).
  useEffect(() => {
    const next = params.get('q') ?? ''
    setQuery(next)
    setDebounced(next.trim())
  }, [params])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(query.trim())
      setParams(query.trim() ? { q: query.trim() } : {}, { replace: true })
    }, 300)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  const results = useAsync(
    () =>
      debounced
        ? Promise.all([api.searchPosts(debounced, 25), api.searchUsers(debounced, 25)])
        : Promise.resolve<[Post[], User[]]>([[], []]),
    [debounced],
  )

  const [posts, people] = results.data ?? [[], []]

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <div className="search-bar" style={{ flex: 1 }}>
            <SearchIcon size={18} />
            <input
              value={query}
              autoFocus
              placeholder="Search posts and people"
              aria-label="Search"
              onChange={(event) => setQuery(event.target.value)}
            />
            {query && (
              <button type="button" className="search-clear" onClick={() => setQuery('')} aria-label="Clear search">
                <CloseIcon size={15} />
              </button>
            )}
          </div>
        </div>
        {debounced && (
          <div className="tabs">
            <button type="button" className={`tab${tab === 'posts' ? ' active' : ''}`} onClick={() => setTab('posts')}>
              Posts {posts.length > 0 && `(${posts.length})`}
            </button>
            <button type="button" className={`tab${tab === 'people' ? ' active' : ''}`} onClick={() => setTab('people')}>
              People {people.length > 0 && `(${people.length})`}
            </button>
          </div>
        )}
      </header>

      {!debounced && (
        <EmptyState
          icon={<SearchIcon size={24} />}
          title="Search Pulse"
          description="Find people by name or username, and posts by their text. Try a hashtag like #math."
        />
      )}

      {debounced && results.loading && <FeedSkeleton rows={4} />}
      {debounced && !results.loading && results.error && (
        <ErrorState message={results.error} onRetry={results.refresh} />
      )}

      {debounced && !results.loading && !results.error && tab === 'posts' && (
        <>
          {posts.length === 0 ? (
            <EmptyState
              icon={<SparkIcon size={24} />}
              title={`No posts match "${debounced}"`}
              description="Try a different word, or search for a person instead."
            />
          ) : (
            posts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                currentUserId={user?.id}
                onChange={(updated) =>
                  results.setData((current) =>
                    current
                      ? [current[0].map((p) => (p.id === updated.id ? updated : p)), current[1]]
                      : current,
                  )
                }
                onDeleted={(id) =>
                  results.setData((current) =>
                    current ? [current[0].filter((p) => p.id !== id), current[1]] : current,
                  )
                }
                onEdit={setEditing}
              />
            ))
          )}
        </>
      )}

      {debounced && !results.loading && !results.error && tab === 'people' && (
        <>
          {people.length === 0 ? (
            <EmptyState
              icon={<SparkIcon size={24} />}
              title={`No people match "${debounced}"`}
              description="Check the spelling, or try searching for a hashtag in the Posts tab."
            />
          ) : (
            people.map((person) => (
              <UserRow
                key={person.id}
                user={person}
                onChange={(updated) =>
                  results.setData((current) =>
                    current
                      ? [current[0], current[1].map((u) => (u.id === updated.id ? updated : u))]
                      : current,
                  )
                }
              />
            ))
          )}
        </>
      )}

      {editing && (
        <EditPostModal
          post={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            results.setData((current) =>
              current ? [current[0].map((p) => (p.id === updated.id ? updated : p)), current[1]] : current,
            )
            setEditing(null)
          }}
        />
      )}
    </>
  )
}
