import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Avatar, DisplayName } from '../components/Avatar'
import { FollowButton } from '../components/FollowButton'
import { PostCard } from '../components/PostCard'
import { ProfileBanner } from '../components/ProfileBanner'
import { UserRow } from '../components/UserRow'
import { EditPostModal } from '../components/EditPostModal'
import { ProfileMenu } from '../components/ProfileMenu'
import { EmptyState, ErrorState, FeedSkeleton, Modal, Spinner } from '../components/UI'
import { InfiniteFeed } from '../components/InfiniteFeed'
import {
  ArrowLeftIcon,
  CalendarIcon,
  LinkIcon,
  LocationIcon,
  MailIcon,
  ShareIcon,
  SparkIcon,
  UsersIcon,
} from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'
import { formatCount, pluralize } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import { usePageTitle } from '../lib/usePageTitle'
import { usePostFeed } from '../lib/usePostFeed'
import type { Post, User } from '../lib/types'

type Tab = 'posts' | 'likes'

/** Opens (or creates) the direct message thread with this person. */
function MessageButton({ username }: { username: string }) {
  const navigate = useNavigate()
  const { notify } = useToast()
  const [busy, setBusy] = useState(false)

  async function open() {
    setBusy(true)
    try {
      const conversation = await api.startConversation(username)
      navigate(`/messages/${conversation.id}`)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Could not open the conversation', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <button type="button" className="btn btn-outline" disabled={busy} onClick={open}>
      {busy ? <Spinner size={16} /> : <MailIcon size={16} />} Message
    </button>
  )
}

/** Copies this profile's public URL, for pasting into another app. */
function ShareProfileButton({ username }: { username: string }) {
  const { notify } = useToast()

  async function copy() {
    // Built from the current origin rather than a configured base so the copied
    // link points at whichever deployment the sharer is actually using.
    const url = `${window.location.origin}/u/${username}`
    try {
      await navigator.clipboard.writeText(url)
      notify('Profile link copied')
    } catch {
      // Clipboard access needs a secure context and permission; the prompt is
      // the fallback so the link is still reachable when it is unavailable.
      window.prompt('Copy this link:', url)
    }
  }

  return (
    <button type="button" className="btn btn-outline" onClick={copy}>
      <ShareIcon size={16} /> Share
    </button>
  )
}

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

  usePageTitle(`/u/${username}`, person ? `${person.display_name} (@${person.username})` : undefined)

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
            <h1 className="page-head-title">
              <DisplayName user={person} />
            </h1>
            <div className="sub">{pluralize(person.post_count, 'post')}</div>
          </div>
          {/* Your own profile only: appearance, notification preferences, the
              account switcher and signing out all live in here. */}
          {isMe && <ProfileMenu />}
        </div>
      </header>

      <ProfileBanner url={person.cover_url} name={person.display_name} />

      <div className="profile-head">
        <div className="profile-avatar">
          <Avatar name={person.display_name} color={person.avatar_color} url={person.avatar_url} size="xl" eager />
        </div>

        <div className="profile-top">
          <div style={{ minWidth: 0 }}>
            <div className="profile-name">
              <DisplayName user={person} />
            </div>
            <div className="profile-handle">@{person.username}</div>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {isMe ? (
              <Link to="/settings" className="btn btn-outline">
                Edit profile
              </Link>
            ) : (
              <>
                <FollowButton user={person} onChange={profile.setData} />
                <MessageButton username={person.username} />
              </>
            )}
            {/* Shown on every profile: the point is to hand the link to someone
                on another platform, which is as true of your own as of anyone's. */}
            <ShareProfileButton username={person.username} />
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
      {!feed.loading && !feed.error && (
        <InfiniteFeed
          hasMore={feed.hasMore}
          loading={feed.loadingMore}
          onLoadMore={feed.loadMore}
          emptyState={
            <EmptyState
              icon={<UsersIcon size={24} />}
              title={tab === 'posts' ? 'No posts yet' : 'No likes yet'}
              description={
                tab === 'posts'
                  ? isMe
                    ? 'Share your first update and it will show up here.'
                    : `${person.display_name} has not posted anything yet.`
                  : `${person.display_name} has not liked any posts yet.`
              }
            />
          }
        >
          {feed.posts.map((post) => (
            <PostCard
              key={post.id}
              post={post}
              currentUserId={me?.id}
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
