import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Avatar, DisplayName } from './Avatar'
import { ThemeToggle } from './ThemeToggle'
import {
  BellIcon,
  CheckIcon,
  CloseIcon,
  CompassIcon,
  ComposeIcon,
  HomeIcon,
  LogoutIcon,
  MailIcon,
  SearchIcon,
  ShieldIcon,
  SparkIcon,
  SwitchIcon,
  TrendIcon,
  UserIcon,
  WifiOffIcon,
} from './Icons'
import { Dropdown, Spinner } from './UI'
import { useAuth } from '../context/AuthContext'
import { useRealtime, useUnreadMessages, useUnreadNotifications } from '../context/RealtimeContext'
import { api } from '../lib/api'
import { formatCount } from '../lib/format'
import type { Stats, TrendingTag, User } from '../lib/types'

const NAV = [
  { to: '/', label: 'Home', icon: HomeIcon, end: true, badge: null },
  { to: '/explore', label: 'Explore', icon: CompassIcon, end: false, badge: null },
  { to: '/notifications', label: 'Notifications', icon: BellIcon, end: false, badge: 'notifications' },
  { to: '/messages', label: 'Messages', icon: MailIcon, end: false, badge: 'messages' },
  { to: '/search', label: 'Search', icon: SearchIcon, end: false, badge: null },
] as const

/** Live counts for the sidebar badges, driven by realtime events. */
function useBadges(): { notifications: number; messages: number } {
  const { count: notifications } = useUnreadNotifications()
  const { count: messages } = useUnreadMessages()
  return { notifications, messages }
}

function Badge({ value, className }: { value: number; className: string }) {
  if (value <= 0) return null
  return <span className={className}>{value > 99 ? '99+' : value}</span>
}

/**
 * The account switcher shown above the signed-in user's row.
 *
 * Lists every account saved on this device and offers a way to add another one
 * (which signs in as a second account, then returns here) or to forget one.
 * The active account is marked and cannot be selected again.
 */
function AccountSwitcher() {
  const { user, accounts, activeAccountId, switchAccount, forgetAccount } = useAuth()
  const navigate = useNavigate()
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (!user || accounts.length === 0) return null

  const others = accounts.filter((account) => account.id !== activeAccountId)

  /**
   * Swap to `id`, keeping the menu open until the switch settles.
   *
   * The menu must stay open on failure: `error` is rendered inside it, so
   * closing first would hide the only explanation the user gets. It closes only
   * once the new session is live.
   */
  async function choose(id: number, close: () => void) {
    setError(null)
    setBusyId(id)
    try {
      await switchAccount(id)
      close()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not switch account')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="account-switcher">
      <Dropdown
        trigger={() => (
          <span className="account-switch-trigger" title="Switch account" aria-label="Switch account">
            <SwitchIcon size={18} />
          </span>
        )}
      >
        {(close) => (
          <div className="account-menu">
            <div className="account-menu-head">Accounts</div>
            {accounts.map((account) => {
              const active = account.id === activeAccountId
              // The active row is rendered from the live session rather than the
              // stored snapshot, so a name or avatar changed in settings shows
              // here immediately instead of after the next sign-in.
              const shown =
                active && user
                  ? {
                      ...account,
                      display_name: user.display_name,
                      username: user.username,
                      avatar_color: user.avatar_color,
                      avatar_url: user.avatar_url,
                    }
                  : account
              return (
                <div key={account.id} className={`account-row${active ? ' active' : ''}`}>
                  <button
                    type="button"
                    className="account-row-main"
                    disabled={active || busyId !== null}
                    onClick={() => void choose(account.id, close)}
                  >
                    <Avatar
                      name={shown.display_name}
                      color={shown.avatar_color}
                      url={shown.avatar_url}
                      size="sm"
                    />
                    <span className="account-row-info">
                      <span className="account-row-name">{shown.display_name}</span>
                      <span className="account-row-handle">@{shown.username}</span>
                    </span>
                    {active && <CheckIcon size={15} />}
                    {busyId === account.id && <Spinner size={14} />}
                  </button>
                  {!active && (
                    <button
                      type="button"
                      className="account-row-forget"
                      onClick={() => forgetAccount(account.id)}
                      aria-label={`Forget @${account.username}`}
                      title="Remove from this device"
                    >
                      <CloseIcon size={13} />
                    </button>
                  )}
                </div>
              )
            })}

            {error && <p className="field-error account-error">{error}</p>}

            <button
              type="button"
              className="account-add"
              onClick={() => {
                close()
                // `add=1` puts the sign-in screen in add-account mode, so the
                // current session is kept and the new account is added to it
                // rather than replacing it.
                navigate('/login?add=1')
              }}
            >
              <UserIcon size={16} /> Add another account
            </button>
            {others.length > 0 && (
              <p className="account-hint">
                {others.length} other {others.length === 1 ? 'account' : 'accounts'} on this device
              </p>
            )}
          </div>
        )}
      </Dropdown>
    </div>
  )
}

export function Sidebar({ onCompose }: { onCompose: () => void }) {
  const { user, logout } = useAuth()
  const badges = useBadges()
  const { connected } = useRealtime()

  return (
    <aside className="sidebar">
      <Link to="/" className="brand">
        <span className="brand-mark">
          <SparkIcon size={19} />
        </span>
        <span className="brand-name">Pulse</span>
      </Link>

      <nav className="nav" aria-label="Primary">
        {NAV.map(({ to, label, icon: Icon, end, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
          >
            <span className="nav-icon">
              <Icon size={23} />
              {badge && <Badge value={badges[badge]} className="nav-badge" />}
            </span>
            <span>{label}</span>
          </NavLink>
        ))}
        {user && (
          <NavLink
            to={`/u/${user.username}`}
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
          >
            <span className="nav-icon">
              <UserIcon size={23} />
            </span>
            <span>Profile</span>
          </NavLink>
        )}
        {user?.is_admin && (
          <NavLink
            to="/admin"
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
          >
            <span className="nav-icon">
              <ShieldIcon size={23} />
            </span>
            <span>Admin</span>
          </NavLink>
        )}
      </nav>

      <button
        type="button"
        className="btn btn-primary btn-block"
        style={{ marginTop: 14 }}
        onClick={onCompose}
      >
        <ComposeIcon size={17} /> Post
      </button>

      <div className="sidebar-spacer" />

      {!connected && (
        <p className="conn-note" title="Live updates are paused. Pulse will reconnect automatically.">
          <WifiOffIcon size={14} /> Reconnecting
        </p>
      )}

      {user && (
        <div className="sidebar-user">
          <Link to={`/u/${user.username}`}>
            <Avatar name={user.display_name} color={user.avatar_color} url={user.avatar_url} size="sm" />
          </Link>
          <Link to={`/u/${user.username}`} className="sidebar-user-info">
            <div className="sidebar-user-name">
              <DisplayName user={user} />
            </div>
            <div className="sidebar-user-handle">@{user.username}</div>
          </Link>
          <AccountSwitcher />
          <button
            type="button"
            className="icon-btn danger"
            onClick={logout}
            title="Sign out"
            aria-label="Sign out"
          >
            <LogoutIcon size={18} />
          </button>
        </div>
      )}
      <div className="sidebar-foot">
        <ThemeToggle />
        <span className="sidebar-foot-label">Appearance</span>
      </div>
    </aside>
  )
}

export function MobileNav() {
  const { user } = useAuth()
  const badges = useBadges()

  return (
    <nav className="mobile-bar" aria-label="Primary">
      {NAV.map(({ to, label, icon: Icon, end, badge }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) => `mobile-nav-item${isActive ? ' active' : ''}`}
        >
          <span className="mobile-nav-icon">
            <Icon size={21} />
            {badge && <Badge value={badges[badge]} className="mobile-nav-badge" />}
          </span>
          <span>{label}</span>
        </NavLink>
      ))}
      {user && (
        <NavLink
          to={`/u/${user.username}`}
          className={({ isActive }) => `mobile-nav-item${isActive ? ' active' : ''}`}
        >
          <UserIcon size={21} />
          <span>Profile</span>
        </NavLink>
      )}
      {user?.is_admin && (
        <NavLink
          to="/admin"
          className={({ isActive }) => `mobile-nav-item${isActive ? ' active' : ''}`}
        >
          <ShieldIcon size={21} />
          <span>Admin</span>
        </NavLink>
      )}
    </nav>
  )
}

export function Rail() {
  const { user } = useAuth()
  const [tags, setTags] = useState<TrendingTag[]>([])
  const [people, setPeople] = useState<User[]>([])
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    let active = true
    Promise.all([api.trendingTags(6), api.trendingPeople(3), api.stats()])
      .then(([tagData, peopleData, statsData]) => {
        if (!active) return
        setTags(tagData)
        setPeople(peopleData)
        setStats(statsData)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [user?.id])

  return (
    <aside className="rail">
      {stats && (
        <section className="card">
          <div className="rail-stats">
            <div className="rail-stat">
              <div className="rail-stat-value">{formatCount(stats.posts)}</div>
              <div className="rail-stat-label">Posts</div>
            </div>
            <div className="rail-stat">
              <div className="rail-stat-value">{formatCount(stats.users)}</div>
              <div className="rail-stat-label">People</div>
            </div>
            <div className="rail-stat">
              <div className="rail-stat-value">{formatCount(stats.likes)}</div>
              <div className="rail-stat-label">Likes</div>
            </div>
            <div className="rail-stat">
              <div className="rail-stat-value">{formatCount(stats.comments)}</div>
              <div className="rail-stat-label">Replies</div>
            </div>
          </div>
        </section>
      )}

      {tags.length > 0 && (
        <section className="card">
          <div className="card-pad" style={{ paddingBottom: 12 }}>
            <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <TrendIcon size={17} /> Trending
            </div>
            <div className="card-sub">Popular hashtags right now</div>
          </div>
          <div className="tag-list">
            {tags.map((tag) => (
              <Link key={tag.name} className="tag-chip" to={`/search?q=${encodeURIComponent('#' + tag.name)}`}>
                #{tag.name}
                <span className="uses">{tag.uses}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {people.length > 0 && (
        <section className="card">
          <div className="card-pad" style={{ paddingBottom: 6 }}>
            <div className="card-title">Popular people</div>
          </div>
          {people.map((person) => (
            <Link key={person.id} to={`/u/${person.username}`} className="suggest-row">
              <Avatar name={person.display_name} color={person.avatar_color} url={person.avatar_url} size="sm" />
              <div className="suggest-info">
                <div className="sidebar-user-name">
                  <DisplayName user={person} />
                </div>
                <div className="sidebar-user-handle">{formatCount(person.follower_count)} followers</div>
              </div>
            </Link>
          ))}
        </section>
      )}
    </aside>
  )
}

export function AppShell({
  children,
  withRail = true,
  onCompose,
}: {
  children: React.ReactNode
  withRail?: boolean
  onCompose: () => void
}) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  // An open conversation owns the bottom-right corner: the thread's send button
  // lives there, so the floating compose button would sit on top of it.
  const inThread = /^\/messages\/\d+/.test(pathname)

  return (
    <div className="app">
      <div className={`shell${withRail ? ' shell-with-rail' : ''}`}>
        <Sidebar onCompose={onCompose} />
        <main className="main">{children}</main>
        {withRail && <Rail />}
      </div>
      {!inThread && (
        <button
          type="button"
          className="mobile-compose"
          onClick={() => navigate('/compose')}
          aria-label="New post"
        >
          <ComposeIcon size={22} />
        </button>
      )}
      <MobileNav />
    </div>
  )
}
