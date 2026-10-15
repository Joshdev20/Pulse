import { useEffect, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { Avatar } from './Avatar'
import {
  BellIcon,
  ComposeIcon,
  HomeIcon,
  LogoutIcon,
  SearchIcon,
  SparkIcon,
  TrendIcon,
  UserIcon,
} from './Icons'
import { useAuth } from '../context/AuthContext'
import { api } from '../lib/api'
import { formatCount } from '../lib/format'
import type { Stats, TrendingTag, User } from '../lib/types'

const NAV = [
  { to: '/', label: 'Home', icon: HomeIcon, end: true },
  { to: '/explore', label: 'Explore', icon: SearchIcon, end: false },
  { to: '/notifications', label: 'Notifications', icon: BellIcon, end: false, badge: true },
  { to: '/search', label: 'Search', icon: UserIcon, end: false },
]

export function useUnreadCount(): number {
  const { user } = useAuth()
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (!user) {
      setCount(0)
      return
    }
    let active = true
    const load = () => {
      api
        .unreadCount()
        .then((result) => {
          if (active) setCount(result.count)
        })
        .catch(() => undefined)
    }
    load()
    const timer = window.setInterval(load, 30_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [user])

  return count
}

export function Sidebar({ onCompose }: { onCompose: () => void }) {
  const { user, logout } = useAuth()
  const unread = useUnreadCount()

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
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
            <span className="nav-icon">
              <Icon size={23} />
              {badge && unread > 0 && <span className="nav-badge">{unread > 99 ? '99+' : unread}</span>}
            </span>
            <span>{label}</span>
          </NavLink>
        ))}
        {user && (
          <NavLink to={`/u/${user.username}`} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
            <span className="nav-icon">
              <UserIcon size={23} />
            </span>
            <span>Profile</span>
          </NavLink>
        )}
      </nav>

      <button type="button" className="btn btn-primary btn-block" style={{ marginTop: 14 }} onClick={onCompose}>
        <ComposeIcon size={17} /> Post
      </button>

      <div className="sidebar-spacer" />

      {user && (
        <div className="sidebar-user">
          <Link to={`/u/${user.username}`}>
            <Avatar name={user.display_name} color={user.avatar_color} size="sm" />
          </Link>
          <Link to={`/u/${user.username}`} className="sidebar-user-info">
            <div className="sidebar-user-name">{user.display_name}</div>
            <div className="sidebar-user-handle">@{user.username}</div>
          </Link>
          <button type="button" className="icon-btn danger" onClick={logout} title="Sign out" aria-label="Sign out">
            <LogoutIcon size={18} />
          </button>
        </div>
      )}
    </aside>
  )
}

export function MobileNav() {
  const { user } = useAuth()
  const unread = useUnreadCount()
  const items = user ? NAV : NAV.slice(0, 2)

  return (
    <nav className="mobile-bar" aria-label="Primary">
      {items.map(({ to, label, icon: Icon, end, badge }) => (
        <NavLink key={to} to={to} end={end} className={({ isActive }) => `mobile-nav-item${isActive ? ' active' : ''}`}>
          <Icon size={21} />
          {badge && unread > 0 && <span className="mobile-nav-badge">{unread > 9 ? '9+' : unread}</span>}
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
              <Avatar name={person.display_name} color={person.avatar_color} size="sm" />
              <div className="suggest-info">
                <div className="sidebar-user-name">{person.display_name}</div>
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
  return (
    <div className="app">
      <div className={`shell${withRail ? ' shell-with-rail' : ''}`}>
        <Sidebar onCompose={onCompose} />
        <main className="main">{children}</main>
        {withRail && <Rail />}
      </div>
      <button
        type="button"
        className="mobile-compose"
        onClick={() => navigate('/compose')}
        aria-label="New post"
      >
        <ComposeIcon size={22} />
      </button>
      <MobileNav />
    </div>
  )
}
