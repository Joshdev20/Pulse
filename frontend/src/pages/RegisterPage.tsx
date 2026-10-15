import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'

export function RegisterPage() {
  const { user, register } = useAuth()
  const navigate = useNavigate()
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    document.title = 'Create account · Pulse'
  }, [])

  if (user) return <Navigate to="/" replace />

  const usernameValid = /^[A-Za-z0-9_]{3,32}$/.test(username)
  const passwordValid = password.length >= 8

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!usernameValid || !passwordValid) return
    setBusy(true)
    setError(null)
    try {
      await register({
        username,
        email,
        display_name: displayName.trim() || username,
        password,
      })
      navigate('/', { replace: true })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not create your account')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-wrap">
      <aside className="auth-aside">
        <Link to="/" className="brand" style={{ padding: 0 }}>
          <span className="brand-mark">
            <SparkIcon size={19} />
          </span>
          <span className="brand-name">Pulse</span>
        </Link>
        <div>
          <h1 className="auth-headline">
            Your corner of <em>the conversation.</em>
          </h1>
          <p className="auth-sub">
            It takes less than a minute to set up a profile and start posting.
          </p>
        </div>
        <div className="auth-points">
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Free to join. No credit card, no setup.</span>
          </div>
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Your posts, replies and likes in one place.</span>
          </div>
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Discover trending topics on the Explore page.</span>
          </div>
        </div>
      </aside>

      <main className="auth-panel">
        <div className="auth-card">
          <h2>Create your account</h2>
          <p className="sub">Pick a username — you can change the rest later.</p>

          <form className="auth-form" onSubmit={submit}>
            {error && <div className="form-error">{error}</div>}

            <div className="field">
              <label htmlFor="display-name">Display name</label>
              <input
                id="display-name"
                className="input"
                value={displayName}
                maxLength={64}
                autoFocus
                placeholder="Ada Lovelace"
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="username">Username</label>
              <input
                id="username"
                className="input"
                value={username}
                required
                placeholder="ada"
                onChange={(event) => setUsername(event.target.value.replace(/\s/g, ''))}
              />
              <span className="field-hint">
                {username && !usernameValid
                  ? 'Use 3–32 letters, numbers or underscores.'
                  : 'Letters, numbers and underscores only.'}
              </span>
            </div>

            <div className="field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                className="input"
                type="email"
                value={email}
                required
                placeholder="ada@example.com"
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                className="input"
                type="password"
                value={password}
                required
                autoComplete="new-password"
                onChange={(event) => setPassword(event.target.value)}
              />
              <span className="field-hint">At least 8 characters.</span>
            </div>

            <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
              {busy ? 'Creating account…' : 'Create account'}
            </button>
          </form>

          <p className="auth-switch">
            Already have an account? <Link to="/login">Sign in</Link>
          </p>
        </div>
      </main>
    </div>
  )
}
