import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Avatar } from '../components/Avatar'
import { ArrowLeftIcon, CheckIcon, LogoutIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'

const COLORS = [
  '#6366f1', '#ec4899', '#f59e0b', '#10b981',
  '#3b82f6', '#8b5cf6', '#ef4444', '#14b8a6',
]

export function SettingsPage() {
  const { user, setUser, logout } = useAuth()
  const { notify } = useToast()
  const navigate = useNavigate()

  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [bio, setBio] = useState(user?.bio ?? '')
  const [location, setLocation] = useState(user?.location ?? '')
  const [website, setWebsite] = useState(user?.website ?? '')
  const [color, setColor] = useState(user?.avatar_color ?? COLORS[0])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!user) return
    setDisplayName(user.display_name)
    setBio(user.bio)
    setLocation(user.location)
    setWebsite(user.website)
    setColor(user.avatar_color)
  }, [user])

  if (!user) return null

  const dirty =
    displayName !== user.display_name ||
    bio !== user.bio ||
    location !== user.location ||
    website !== user.website ||
    color !== user.avatar_color

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!user) return
    setBusy(true)
    setError(null)
    try {
      const updated = await api.updateProfile(user.username, {
        display_name: displayName.trim(),
        bio,
        location,
        website,
        avatar_color: color,
      })
      setUser(updated)
      notify('Profile saved')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save your profile')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <header className="page-head">
        <div className="page-head-inner">
          <button type="button" className="icon-btn" onClick={() => navigate(-1)} aria-label="Go back">
            <ArrowLeftIcon size={20} />
          </button>
          <div style={{ flex: 1 }}>
            <h1>Settings</h1>
            <div className="sub">Update how you appear on Pulse</div>
          </div>
        </div>
      </header>

      <form onSubmit={save} style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 20 }}>
        {error && <div className="form-error">{error}</div>}

        <section className="card card-pad">
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 18 }}>
            <Avatar name={displayName || user.username} color={color} size="xl" />
            <div>
              <div className="card-title">Avatar colour</div>
              <div className="card-sub">Your initials are shown on this colour.</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                {COLORS.map((swatch) => (
                  <button
                    key={swatch}
                    type="button"
                    aria-label={`Use colour ${swatch}`}
                    aria-pressed={color === swatch}
                    onClick={() => setColor(swatch)}
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: '50%',
                      background: swatch,
                      border: color === swatch ? '2px solid var(--text)' : '2px solid transparent',
                      display: 'grid',
                      placeItems: 'center',
                      color: 'rgba(0,0,0,0.7)',
                    }}
                  >
                    {color === swatch && <CheckIcon size={15} />}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="field">
            <label htmlFor="display-name">Display name</label>
            <input
              id="display-name"
              className="input"
              value={displayName}
              maxLength={64}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>

          <div className="field" style={{ marginTop: 14 }}>
            <label htmlFor="bio">Bio</label>
            <textarea
              id="bio"
              className="textarea"
              value={bio}
              maxLength={280}
              placeholder="Tell people a little about yourself"
              onChange={(event) => setBio(event.target.value)}
            />
            <span className="field-hint">{280 - bio.length} characters left</span>
          </div>

          <div className="field" style={{ marginTop: 14 }}>
            <label htmlFor="location">Location</label>
            <input
              id="location"
              className="input"
              value={location}
              maxLength={64}
              placeholder="City, Country"
              onChange={(event) => setLocation(event.target.value)}
            />
          </div>

          <div className="field" style={{ marginTop: 14 }}>
            <label htmlFor="website">Website</label>
            <input
              id="website"
              className="input"
              value={website}
              maxLength={255}
              placeholder="https://example.com"
              onChange={(event) => setWebsite(event.target.value)}
            />
          </div>
        </section>

        <section className="card card-pad">
          <div className="card-title">Account</div>
          <div className="card-sub" style={{ marginBottom: 14 }}>
            Signed in as <strong style={{ color: 'var(--text)' }}>@{user.username}</strong>
          </div>
          <button type="button" className="btn btn-outline" onClick={logout}>
            <LogoutIcon size={17} /> Sign out
          </button>
        </section>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={!dirty || busy}
            onClick={() => {
              setDisplayName(user.display_name)
              setBio(user.bio)
              setLocation(user.location)
              setWebsite(user.website)
              setColor(user.avatar_color)
            }}
          >
            Reset
          </button>
          <button type="submit" className="btn btn-primary" disabled={!dirty || busy || !displayName.trim()}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>
    </>
  )
}
