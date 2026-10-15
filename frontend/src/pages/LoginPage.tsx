import { useEffect, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { EyeIcon, EyeOffIcon, SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'

export function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    document.title = 'Sign in · Pulse'
  }, [])

  if (user) {
    const from = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={from} replace />
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      const from = (location.state as { from?: string } | null)?.from ?? '/'
      navigate(from, { replace: true })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not sign in')
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
            Short updates. <em>Real conversations.</em>
          </h1>
          <p className="auth-sub">
            Follow the people and topics you care about, and share what you are working on.
          </p>
        </div>
        <div className="auth-points">
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Post updates with photos, video and hashtags.</span>
          </div>
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Build a timeline from the people you follow.</span>
          </div>
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Message anyone privately, with live delivery.</span>
          </div>
          <div className="auth-point">
            <span className="auth-point-icon">
              <SparkIcon size={16} />
            </span>
            <span>Get notified the moment someone replies or likes your post.</span>
          </div>
        </div>
      </aside>

      <main className="auth-panel">
        <div className="auth-card">
          <h2>Welcome back</h2>
          <p className="sub">Sign in to continue to your timeline.</p>

          <form className="auth-form" onSubmit={submit}>
            {error && <div className="form-error">{error}</div>}
            <div className="field">
              <label htmlFor="username">Username or email</label>
              <input
                id="username"
                className="input"
                value={username}
                autoComplete="username"
                autoFocus
                required
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <div className="input-affix">
                <input
                  id="password"
                  className="input"
                  type={reveal ? 'text' : 'password'}
                  value={password}
                  autoComplete="current-password"
                  required
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  className="input-affix-btn"
                  onClick={() => setReveal((value) => !value)}
                  aria-label={reveal ? 'Hide password' : 'Show password'}
                  aria-pressed={reveal}
                  title={reveal ? 'Hide password' : 'Show password'}
                >
                  {reveal ? <EyeOffIcon size={17} /> : <EyeIcon size={17} />}
                </button>
              </div>
            </div>
            <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <p className="auth-switch">
            <Link to="/forgot-password">Forgot your password?</Link>
          </p>
          <p className="auth-switch" style={{ marginTop: 8 }}>
            New here? <Link to="/register">Create an account</Link>
          </p>
        </div>
      </main>
    </div>
  )
}
