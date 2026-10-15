import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar } from '../components/Avatar'
import { FollowButton } from '../components/FollowButton'
import { PostCard } from '../components/PostCard'
import { UserRow } from '../components/UserRow'
import { EditPostModal } from '../components/EditPostModal'
import { EmptyState, ErrorState, FeedSkeleton, Modal, Spinner } from '../components/UI'
import { ArrowLeftIcon, CalendarIcon, LinkIcon, LocationIcon, SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { formatCount, pluralize } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import { usePostFeed } from '../lib/usePostFeed'
import type { Post, User } from '../lib/types'

type Tab = 'posts' | 'likes'

export function ProfilePage() {
  const { username = '' } = useParams()
  const navigate = useNavigate()
  const { user: me, setUser } = useAuth()
  const [tab, setTab] = useState<Tab>('posts')
  const [editing, setEditing] = useState<Post | null>(null)
  const [listModal, setListModal] = useState<'followers' | 'following' | null>(null)

  const profile = useAsync(() => api.user(username), [username, me?.id])
  const person = profile.data
  const isMe = me?.username.toLowerCase() === username.toLowerCase()

  const feed = usePostFeed(
    (page) => (tab === 'posts' ? api.userPosts(username, page, 20) : api.userLikes(username, page, 20)),
    [username, tab, me?.id],
  )

  const lists = useAsync(
    () =>
      listModal
        ? listModal === 'followers'
          ? api.followers(username)
          : api.following(username)
        : Promise.resolve<User[]>([]),
    [listModal, username],
  )

  // Keep the signed-in user's own header in sync after following someone from here.
  useEffect(() => {
    if (isMe && person && me && person.follower_count !== me.follower_count) setUser(person)
  }, [isMe, person, me, setUser])

  if (profile.loading) return <FeedSkeleton rows={4} />
  if (profile.error || !person) {
    return (
      <ErrorState
        message={profile.error ?? 'This account does not exist'}
        onRetry={profile.refresh}
      />
    )
  }

  const joined = new Date(person.created_at).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  })

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <button type="button" className="icon-btn" onClick={() => navigate(-1)} aria-label="Go back">
            <ArrowLeftIcon size={20} />
          </button>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {person.display_name}
            </h1>
            <div className="sub">{pluralize(person.post_count, 'post')}</div>
          </div>
        </div>
      </header>

      <div className="profile-banner" />

      <div className="profile-head">
        <div className="profile-avatar">
          <Avatar name={person.display_name} color={person.avatar_color} size="xl" />
        </div>

        <div className="profile-top">
          <div style={{ minWidth: 0 }}>
            <div className="profile-name">{person.display_name}</div>
            <div className="profile-handle">@{person.username}</div>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            {isMe ? (
              <Link to="/settings" className="btn btn-outline">
                Edit profile
              </Link>
            ) : (
              <FollowButton user={person} onChange={profile.setData} />
            )}
          </div>
        </div>

        {person.bio && <p className="profile-bio">{person.bio}</p>}

        <div className="profile-meta">
          {person.location && (
            <span className="profile-meta-item">
              <LocationIcon /> {person.location}
            </span>
          )}
          {person.website && (
            <span className="profile-meta-item">
              <LinkIcon />
              <a href={person.website} target="_blank" rel="noreferrer noopener">
                {person.website.replace(/^https?:\/\//, '')}
              </a>
            </span>
          )}
          <span className="profile-meta-item">
            <CalendarIcon /> Joined {joined}
          </span>
        </div>

        <div className="profile-counts">
          <button type="button" onClick={() => setListModal('following')}>
            <strong>{formatCount(person.following_count)}</strong> Following
          </button>
          <button type="button" onClick={() => setListModal('followers')}>
            <strong>{formatCount(person.follower_count)}</strong> Followers
          </button>
        </div>
      </div>

      <div className="tabs">
        <button type="button" className={`tab${tab === 'posts' ? ' active' : ''}`} onClick={() => setTab('posts')}>
          Posts
        </button>
        <button type="button" className={`tab${tab === 'likes' ? ' active' : ''}`} onClick={() => setTab('likes')}>
          Likes
        </button>
      </div>

      {feed.loading && <FeedSkeleton rows={4} />}
      {!feed.loading && feed.error && <ErrorState message={feed.error} onRetry={feed.reload} />}
      {!feed.loading && !feed.error && feed.posts.length === 0 && (
        <EmptyState
          icon={<SparkIcon size={24} />}
          title={tab === 'posts' ? 'No posts yet' : 'No likes yet'}
          description={
            tab === 'posts'
              ? isMe
                ? 'Share your first update and it will show up here.'
                : `${person.display_name} has not posted anything yet.`
              : `${person.display_name} has not liked any posts yet.`
          }
        />
      )}
      {!feed.loading &&
        feed.posts.map((post) => (
          <PostCard
            key={post.id}
            post={post}
            currentUserId={me?.id}
            onChange={feed.replace}
            onDeleted={feed.remove}
            onEdit={setEditing}
          />
        ))}
      {!feed.loading && feed.hasMore && (
        <div className="load-more">
          <button type="button" className="btn btn-secondary" disabled={feed.loadingMore} onClick={feed.loadMore}>
            {feed.loadingMore ? <Spinner size={16} /> : 'Load more'}
          </button>
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

      {listModal && (
        <Modal
          title={listModal === 'followers' ? 'Followers' : 'Following'}
          onClose={() => setListModal(null)}
        >
          {lists.loading && <FeedSkeleton rows={3} />}
          {!lists.loading && (lists.data ?? []).length === 0 && (
            <EmptyState
              icon={<SparkIcon size={24} />}
              title={listModal === 'followers' ? 'No followers yet' : 'Not following anyone'}
              description={
                listModal === 'followers'
                  ? 'When people follow this account, they will appear here.'
                  : 'Accounts this person follows will appear here.'
              }
            />
          )}
          {(lists.data ?? []).map((person2) => (
            <UserRow
              key={person2.id}
              user={person2}
              onChange={(updated) =>
                lists.setData((current) =>
                  current?.map((entry) => (entry.id === updated.id ? updated : entry)) ?? null,
                )
              }
            />
          ))}
        </Modal>
      )}
    </>
  )
}
