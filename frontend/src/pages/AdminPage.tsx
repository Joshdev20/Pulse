import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Avatar } from '../components/Avatar'
import { EmptyState, ErrorState, Modal, Spinner } from '../components/UI'
import {
  BanIcon,
  CheckIcon,
  SearchIcon,
  ShieldIcon,
  SparkIcon,
  TrashIcon,
  UsersIcon,
  WarningIcon,
} from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { formatCount, formatFullDate, pluralize } from '../lib/format'
import type { AdminComment, AdminPost, AdminUser, AdminUserStatus, Page } from '../lib/types'

/** Wait for typing to settle before hitting the search endpoint. */
function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return settled
}

function StatTile({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="admin-tile">
      <div className="admin-tile-value">{value}</div>
      <div className="admin-tile-label">{label}</div>
      {hint && <div className="admin-tile-hint">{hint}</div>}
    </div>
  )
}

function Panel({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <section className="card admin-panel">
      <div className="card-pad">
        <div className="card-title">{title}</div>
        {description && <div className="card-sub">{description}</div>}
      </div>
      {children}
    </section>
  )
}

/** A compact horizontal bar for a ranked list such as top posters. */
function RankList({ items, empty }: { items: { label: string; value: number }[]; empty: string }) {
  if (items.length === 0) return <p className="admin-empty">{empty}</p>
  const peak = Math.max(...items.map((item) => item.value), 1)
  return (
    <div className="admin-ranks">
      {items.map((item) => (
        <div className="admin-rank" key={item.label}>
          <span className="admin-rank-label" title={item.label}>
            {item.label}
          </span>
          <span className="admin-rank-bar">
            <span style={{ width: `${(item.value / peak) * 100}%` }} />
          </span>
          <span className="admin-rank-value">{formatCount(item.value)}</span>
        </div>
      ))}
    </div>
  )
}

function Pager({
  page,
  pageSize,
  total,
  busy,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  busy: boolean
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (pages <= 1) return null
  return (
    <div className="admin-pager">
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        disabled={page <= 1 || busy}
        onClick={() => onPage(page - 1)}
      >
        Previous
      </button>
      <span className="admin-pager-status">
        Page {page} of {pages}
      </span>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        disabled={page >= pages || busy}
        onClick={() => onPage(page + 1)}
      >
        Next
      </button>
    </div>
  )
}

function OverviewTab() {
  const stats = useAsync(() => api.adminStats(), [])

  if (stats.loading) {
    return (
      <div className="state">
        <Spinner size={22} />
      </div>
    )
  }
  if (stats.error || !stats.data) {
    return <ErrorState message={stats.error ?? 'Could not load analytics'} onRetry={stats.refresh} />
  }
  const data = stats.data

  return (
    <div className="admin-tab-body">
      <section className="admin-tiles">
        <StatTile label="Total users" value={formatCount(data.total_users)} hint={`${data.active_users} active`} />
        <StatTile label="Total posts" value={formatCount(data.total_posts)} hint={`${data.posts_7d} this week`} />
        <StatTile label="Replies" value={formatCount(data.total_comments)} />
        <StatTile label="Likes" value={formatCount(data.total_likes)} />
        <StatTile
          label="Engagement"
          value={data.engagement_rate}
          hint="reactions per post"
        />
        <StatTile
          label="Posts per user"
          value={data.avg_posts_per_user}
          hint="lifetime average"
        />
        <StatTile
          label="Banned"
          value={formatCount(data.banned_users)}
          hint={`${data.admin_users} moderator${data.admin_users === 1 ? '' : 's'}`}
        />
        <StatTile
          label="New users"
          value={formatCount(data.new_users_30d)}
          hint={`${data.new_users_7d} in the last 7 days`}
        />
      </section>

      <div className="admin-split">
        <Panel title="Top posters" description="Most posts authored">
          <div className="card-pad" style={{ paddingTop: 4 }}>
            <RankList items={data.top_posters} empty="No posts yet." />
          </div>
        </Panel>
        <Panel title="Trending tags" description="Most used hashtags">
          <div className="card-pad" style={{ paddingTop: 4 }}>
            <RankList items={data.top_tags} empty="No hashtags yet." />
          </div>
        </Panel>
      </div>

      <Panel title="Messaging and media" description="Direct messages and uploaded files">
        <div className="card-pad" style={{ paddingTop: 4 }}>
          <div className="admin-inline-stats">
            <span>
              <strong>{formatCount(data.total_conversations)}</strong> conversations
            </span>
            <span>
              <strong>{formatCount(data.total_messages)}</strong> messages
            </span>
            <span>
              <strong>{formatCount(data.total_media)}</strong> media files
            </span>
            <span>
              <strong>{formatCount(data.posts_30d)}</strong> posts in 30 days
            </span>
          </div>
        </div>
      </Panel>
    </div>
  )
}

function UserRow({
  user,
  selfId,
  busy,
  onBan,
  onToggleAdmin,
  onDelete,
}: {
  user: AdminUser
  selfId: number | undefined
  busy: boolean
  onBan: (user: AdminUser) => void
  onToggleAdmin: (user: AdminUser) => void
  onDelete: (user: AdminUser) => void
}) {
  const isSelf = user.id === selfId
  return (
    <div className={`admin-row${user.is_banned ? ' admin-row-banned' : ''}`}>
      <Link to={`/u/${user.username}`} className="admin-row-avatar">
        <Avatar name={user.display_name} color={user.avatar_color} size="md" />
      </Link>

      <div className="admin-row-main">
        <div className="admin-row-title">
          <Link to={`/u/${user.username}`} className="admin-row-name">
            {user.display_name}
          </Link>
          <span className="admin-row-handle">@{user.username}</span>
          {user.is_admin && <span className="admin-badge admin-badge-accent">Moderator</span>}
          {user.is_banned && <span className="admin-badge admin-badge-danger">Banned</span>}
          {isSelf && <span className="admin-badge">You</span>}
        </div>
        <div className="admin-row-sub">
          {user.email} · joined {formatFullDate(user.created_at)}
        </div>
        <div className="admin-row-stats">
          <span>{pluralize(user.post_count, 'post')}</span>
          <span>{pluralize(user.comment_count, 'reply', 'replies')}</span>
          <span>{pluralize(user.follower_count, 'follower')}</span>
          <span>{user.following_count} following</span>
        </div>
        {user.is_banned && user.banned_reason && (
          <div className="admin-row-reason">Reason: {user.banned_reason}</div>
        )}
      </div>

      <div className="admin-row-actions">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={busy || isSelf}
          title={isSelf ? 'You cannot change your own role' : undefined}
          onClick={() => onToggleAdmin(user)}
        >
          <ShieldIcon size={15} /> {user.is_admin ? 'Revoke' : 'Make admin'}
        </button>
        <button
          type="button"
          className={`btn btn-sm ${user.is_banned ? 'btn-secondary' : 'btn-outline'}`}
          disabled={busy || isSelf}
          title={isSelf ? 'You cannot ban your own account' : undefined}
          onClick={() => onBan(user)}
        >
          {user.is_banned ? <CheckIcon size={15} /> : <BanIcon size={15} />}
          {user.is_banned ? 'Unban' : 'Ban'}
        </button>
        <button
          type="button"
          className="btn btn-danger btn-sm"
          disabled={busy || isSelf}
          title={isSelf ? 'You cannot delete your own account' : undefined}
          onClick={() => onDelete(user)}
        >
          <TrashIcon size={15} /> Delete
        </button>
      </div>
    </div>
  )
}

function UsersTab() {
  const { user: me } = useAuth()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<AdminUserStatus>('all')
  const [page, setPage] = useState(1)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [banTarget, setBanTarget] = useState<AdminUser | null>(null)
  const [banReason, setBanReason] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null)

  const query = useDebounced(search)
  const users = useAsync(
    () => api.adminUsers({ q: query, status, page, page_size: 20 }),
    [query, status, page],
  )

  // A new search or filter invalidates the current page number.
  useEffect(() => {
    setPage(1)
  }, [query, status])

  const replace = useCallback(
    (updated: AdminUser) => {
      users.setData((current) =>
        current
          ? { ...current, items: current.items.map((item) => (item.id === updated.id ? updated : item)) }
          : current,
      )
    },
    [users],
  )

  const remove = useCallback(
    (id: number) => {
      users.setData((current) =>
        current
          ? { ...current, items: current.items.filter((item) => item.id !== id), total: current.total - 1 }
          : current,
      )
    },
    [users],
  )

  async function run(id: number, action: () => Promise<void>) {
    setBusyId(id)
    setActionError(null)
    try {
      await action()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : 'The action failed')
    } finally {
      setBusyId(null)
    }
  }

  async function confirmBan() {
    if (!banTarget) return
    const target = banTarget
    await run(target.id, async () => {
      replace(await api.adminSetBan(target.id, true, banReason))
      setBanTarget(null)
      setBanReason('')
    })
  }

  const items = users.data?.items ?? []

  return (
    <div className="admin-tab-body">
      <div className="admin-toolbar">
        <label className="admin-search">
          <SearchIcon size={17} />
          <input
            type="search"
            value={search}
            placeholder="Search name, username or email"
            aria-label="Search users"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="admin-filters" role="group" aria-label="Filter by status">
          {(['all', 'active', 'banned', 'admin'] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={`tab${status === value ? ' active' : ''}`}
              onClick={() => setStatus(value)}
            >
              {value === 'all' ? 'All' : value === 'admin' ? 'Moderators' : value[0].toUpperCase() + value.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {actionError && <p className="admin-error">{actionError}</p>}

      {users.loading && (
        <div className="state">
          <Spinner size={22} />
        </div>
      )}

      {!users.loading && users.error && <ErrorState message={users.error} onRetry={users.refresh} />}

      {!users.loading && !users.error && items.length === 0 && (
        <EmptyState
          icon={<UsersIcon size={24} />}
          title="No matching users"
          description={
            query || status !== 'all'
              ? 'Try a different search term or filter.'
              : 'Once people sign up they will appear here.'
          }
        />
      )}

      {!users.loading && !users.error && items.length > 0 && (
        <>
          <div className="admin-count">
            {formatCount(users.data?.total ?? 0)} {users.data?.total === 1 ? 'user' : 'users'}
          </div>
          <div className="admin-rows">
            {items.map((entry) => (
              <UserRow
                key={entry.id}
                user={entry}
                selfId={me?.id}
                busy={busyId === entry.id}
                onBan={(target) => {
                  if (target.is_banned) {
                    void run(target.id, async () => {
                      replace(await api.adminSetBan(target.id, false))
                    })
                  } else {
                    setBanReason('')
                    setBanTarget(target)
                  }
                }}
                onToggleAdmin={(target) =>
                  void run(target.id, async () => {
                    replace(await api.adminSetAdmin(target.id, !target.is_admin))
                  })
                }
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
          <Pager
            page={users.data?.page ?? 1}
            pageSize={users.data?.page_size ?? 20}
            total={users.data?.total ?? 0}
            busy={users.loading}
            onPage={setPage}
          />
        </>
      )}

      {banTarget && (
        <Modal
          title={`Ban @${banTarget.username}?`}
          onClose={() => setBanTarget(null)}
          footer={
            <>
              <button type="button" className="btn btn-secondary" onClick={() => setBanTarget(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busyId === banTarget.id}
                onClick={() => void confirmBan()}
              >
                Ban user
              </button>
            </>
          }
        >
          <p className="admin-modal-note">
            <WarningIcon size={16} /> They will be signed out immediately and cannot sign in again until unbanned.
            Their posts stay visible unless you remove them separately.
          </p>
          <label className="admin-field">
            <span>Reason (optional)</span>
            <input
              type="text"
              value={banReason}
              maxLength={280}
              placeholder="Shown to other moderators"
              onChange={(event) => setBanReason(event.target.value)}
            />
          </label>
        </Modal>
      )}

      {deleteTarget && (
        <Modal
          title={`Delete @${deleteTarget.username}?`}
          onClose={() => setDeleteTarget(null)}
          footer={
            <>
              <button type="button" className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busyId === deleteTarget.id}
                onClick={() =>
                  void run(deleteTarget.id, async () => {
                    await api.adminDeleteUser(deleteTarget.id)
                    remove(deleteTarget.id)
                    setDeleteTarget(null)
                  })
                }
              >
                Delete permanently
              </button>
            </>
          }
        >
          <p className="admin-modal-note">
            <WarningIcon size={16} /> This permanently removes the account together with
            {` ${pluralize(deleteTarget.post_count, 'post')}`}, {pluralize(deleteTarget.comment_count, 'reply', 'replies')},
            their likes and their messages. It cannot be undone.
          </p>
        </Modal>
      )}
    </div>
  )
}

function ModerationTab() {
  const [kind, setKind] = useState<'posts' | 'comments'>('posts')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const query = useDebounced(search)

  // One loader for both queues keeps `setData` a single type rather than a
  // union of two dispatchers. The two page shapes are identical, so widening
  // the item type is accurate rather than a workaround.
  const queue = useAsync<Page<AdminPost | AdminComment>>(
    async () =>
      kind === 'posts'
        ? ((await api.adminPosts({ q: query, page, page_size: 20 })) as Page<AdminPost | AdminComment>)
        : ((await api.adminComments({ q: query, page, page_size: 20 })) as Page<AdminPost | AdminComment>),
    [kind, query, page],
  )

  useEffect(() => {
    setPage(1)
  }, [kind, query])

  async function remove(id: number, action: () => Promise<void>) {
    setBusyId(id)
    setActionError(null)
    try {
      await action()
      queue.setData((current) =>
        current
          ? { ...current, items: current.items.filter((item) => item.id !== id), total: current.total - 1 }
          : current,
      )
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : 'The action failed')
    } finally {
      setBusyId(null)
    }
  }

  const items = queue.data?.items ?? []

  return (
    <div className="admin-tab-body">
      <div className="admin-toolbar">
        <label className="admin-search">
          <SearchIcon size={17} />
          <input
            type="search"
            value={search}
            placeholder={kind === 'posts' ? 'Search post text' : 'Search replies'}
            aria-label={kind === 'posts' ? 'Search posts' : 'Search comments'}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="admin-filters" role="group" aria-label="Content type">
          <button
            type="button"
            className={`tab${kind === 'posts' ? ' active' : ''}`}
            onClick={() => setKind('posts')}
          >
            Posts
          </button>
          <button
            type="button"
            className={`tab${kind === 'comments' ? ' active' : ''}`}
            onClick={() => setKind('comments')}
          >
            Replies
          </button>
        </div>
      </div>

      {actionError && <p className="admin-error">{actionError}</p>}

      {queue.loading && (
        <div className="state">
          <Spinner size={22} />
        </div>
      )}

      {!queue.loading && queue.error && <ErrorState message={queue.error} onRetry={queue.refresh} />}

      {!queue.loading && !queue.error && items.length === 0 && (
        <EmptyState
          icon={<SparkIcon size={24} />}
          title={kind === 'posts' ? 'No posts to review' : 'No replies to review'}
          description={query ? 'Nothing matched that search.' : 'Content will appear here as people post.'}
        />
      )}

      {!queue.loading && !queue.error && items.length > 0 && (
        <>
          <div className="admin-count">
            {formatCount(queue.data?.total ?? 0)} {kind === 'posts' ? 'posts' : 'replies'}
          </div>
          <div className="admin-rows">
            {kind === 'posts'
              ? (items as AdminPost[]).map((post) => (
                  <div className="admin-row admin-row-content" key={post.id}>
                    <div className="admin-row-main">
                      <div className="admin-row-title">
                        <Link to={`/u/${post.author_username}`} className="admin-row-name">
                          {post.author_display_name}
                        </Link>
                        <span className="admin-row-handle">@{post.author_username}</span>
                      </div>
                      <p className="admin-content-body">{post.body || '(no text)'}</p>
                      <div className="admin-row-stats">
                        <span>{pluralize(post.like_count, 'like')}</span>
                        <span>{pluralize(post.comment_count, 'reply', 'replies')}</span>
                        {post.media_count > 0 && <span>{post.media_count} media</span>}
                        <Link to={`/p/${post.id}`} className="admin-link">
                          View post
                        </Link>
                      </div>
                    </div>
                    <div className="admin-row-actions">
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        disabled={busyId === post.id}
                        onClick={() => void remove(post.id, () => api.adminDeletePost(post.id))}
                      >
                        <TrashIcon size={15} /> Delete
                      </button>
                    </div>
                  </div>
                ))
              : (items as AdminComment[]).map((comment) => (
                  <div className="admin-row admin-row-content" key={comment.id}>
                    <div className="admin-row-main">
                      <div className="admin-row-title">
                        <Link to={`/u/${comment.author_username}`} className="admin-row-name">
                          {comment.author_display_name}
                        </Link>
                        <span className="admin-row-handle">@{comment.author_username}</span>
                      </div>
                      <p className="admin-content-body">{comment.body}</p>
                      <div className="admin-row-stats">
                        <Link to={`/p/${comment.post_id}`} className="admin-link">
                          View post
                        </Link>
                      </div>
                    </div>
                    <div className="admin-row-actions">
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        disabled={busyId === comment.id}
                        onClick={() => void remove(comment.id, () => api.adminDeleteComment(comment.id))}
                      >
                        <TrashIcon size={15} /> Delete
                      </button>
                    </div>
                  </div>
                ))}
          </div>
          <Pager
            page={queue.data?.page ?? 1}
            pageSize={queue.data?.page_size ?? 20}
            total={queue.data?.total ?? 0}
            busy={queue.loading}
            onPage={setPage}
          />
        </>
      )}
    </div>
  )
}

/**
 * Moderation console.
 *
 * The route is also guarded by `RequireAuth`; this in-page check is defence in
 * depth so a non-moderator who reaches the URL never fires an admin request.
 */
export function AdminPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState<'overview' | 'users' | 'moderation'>('overview')

  if (!user?.is_admin) {
    return (
      <EmptyState
        icon={<ShieldIcon size={24} />}
        title="Moderators only"
        description="This area is limited to platform moderators."
        action={
          <Link to="/" className="btn btn-secondary btn-sm">
            Back to home
          </Link>
        }
      />
    )
  }

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <div style={{ flex: 1 }}>
            <h1>Admin</h1>
            <div className="sub">Analytics, users and moderation</div>
          </div>
          <ShieldIcon size={22} />
        </div>
        <div className="tabs">
          <button
            type="button"
            className={`tab${tab === 'overview' ? ' active' : ''}`}
            onClick={() => setTab('overview')}
          >
            Overview
          </button>
          <button
            type="button"
            className={`tab${tab === 'users' ? ' active' : ''}`}
            onClick={() => setTab('users')}
          >
            Users
          </button>
          <button
            type="button"
            className={`tab${tab === 'moderation' ? ' active' : ''}`}
            onClick={() => setTab('moderation')}
          >
            Moderation
          </button>
        </div>
      </header>

      {tab === 'overview' && <OverviewTab />}
      {tab === 'users' && <UsersTab />}
      {tab === 'moderation' && <ModerationTab />}
    </>
  )
}
