import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Avatar } from '../components/Avatar'
import { ArrowLeftIcon, CheckIcon, LogoutIcon, TrashIcon, UploadIcon } from '../components/Icons'
import { Spinner } from '../components/UI'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { api } from '../lib/api'

const COLORS = [
  '#6366f1', '#ec4899', '#f59e0b', '#10b981',
  '#3b82f6', '#8b5cf6', '#ef4444', '#14b8a6',
]

const MAX_AVATAR_MB = 5

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
  const [photoBusy, setPhotoBusy] = useState(false)
  const [photoProgress, setPhotoProgress] = useState(0)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

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

  async function uploadPhoto(file: File) {
    setPhotoError(null)
    // Checked here as well as on the server so a large file fails instantly
    // instead of after a long upload.
    if (!file.type.startsWith('image/')) {
      setPhotoError('Choose an image file')
      return
    }
    if (file.size > MAX_AVATAR_MB * 1024 * 1024) {
      setPhotoError(`Images must be under ${MAX_AVATAR_MB}MB`)
      return
    }

    setPhotoBusy(true)
    setPhotoProgress(0)
    try {
      const updated = await api.uploadAvatar(file, setPhotoProgress)
      setUser(updated)
      notify('Profile picture updated')
    } catch (caught) {
      setPhotoError(caught instanceof Error ? caught.message : 'Could not upload that image')
    } finally {
      setPhotoBusy(false)
      setPhotoProgress(0)
    }
  }

  async function removePhoto() {
    setPhotoError(null)
    setPhotoBusy(true)
    try {
      const updated = await api.clearAvatar()
      setUser(updated)
      notify('Profile picture removed')
    } catch (caught) {
      setPhotoError(caught instanceof Error ? caught.message : 'Could not remove your picture')
    } finally {
      setPhotoBusy(false)
    }
  }

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
          <div className="avatar-editor">
            <Avatar
              name={displayName || user.username}
              color={color}
              url={user.avatar_url}
              size="xl"
            />
            <div className="avatar-editor-body">
              <div className="card-title">Profile picture</div>
              <div className="card-sub">
                {user.avatar_url
                  ? 'Uploaded picture. Remove it to go back to your initials.'
                  : 'Upload a JPEG, PNG, GIF or WebP up to 5MB.'}
              </div>

              {photoError && <div className="form-error">{photoError}</div>}

              <div className="avatar-actions">
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={photoBusy}
                  onClick={() => fileRef.current?.click()}
                >
                  {photoBusy ? <Spinner size={16} /> : <UploadIcon size={16} />}
                  {photoBusy ? 'Uploading…' : user.avatar_url ? 'Replace' : 'Upload'}
                </button>
                {user.avatar_url && (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={photoBusy}
                    onClick={removePhoto}
                  >
                    <TrashIcon size={16} /> Remove
                  </button>
                )}
              </div>

              {photoBusy && photoProgress > 0 && (
                <div
                  className="upload-bar"
                  role="progressbar"
                  aria-valuenow={photoProgress}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <span className="upload-bar-fill" style={{ width: `${photoProgress}%` }} />
                </div>
              )}

              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  // Reset so picking the same file twice fires a change again.
                  event.target.value = ''
                  if (file) void uploadPhoto(file)
                }}
              />
            </div>
          </div>

          <div className="field" style={{ marginTop: 18 }}>
            <div className="card-title">Avatar colour</div>
            <div className="card-sub">Used for your initials when no picture is set.</div>
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
