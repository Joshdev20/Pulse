import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { EyeIcon, EyeOffIcon, SparkIcon } from '../components/Icons'
import { useAuth } from '../context/AuthContext'
import { api, setToken } from '../lib/api'

/** Choose a new password using the token from the emailed link. */
export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { refresh } = useAuth()
  const token = params.get('token') ?? ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    document.title = 'Choose a new password · Pulse'
  }, [])

  if (!token) {
    return (
      <AuthShell headline="That link is incomplete">
        <h2>Link not valid</h2>
        <p className="sub">
          This page needs the token from your reset email. Request a new link to continue.
        </p>
        <p className="auth-switch">
          <Link to="/forgot-password">Request a new link</Link>
        </p>
      </AuthShell>
    )
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (password !== confirm) {
      setError('Those passwords do not match')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { access_token } = await api.resetPassword(token, password)
      // The reset endpoint signs the user in, so store the token and load the
      // profile before landing on the timeline.
      setToken(access_token)
      await refresh()
      navigate('/', { replace: true })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not reset your password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell headline="Choose something you will remember">
      <h2>Set a new password</h2>
      <p className="sub">You will be signed in once it is saved.</p>
      <form className="auth-form" onSubmit={submit}>
        {error && <div className="form-error">{error}</div>}
        <div className="field">
          <label htmlFor="password">New password</label>
          <div className="input-affix">
            <input
              id="password"
              className="input"
              type={reveal ? 'text' : 'password'}
              value={password}
              autoComplete="new-password"
              autoFocus
              required
              minLength={8}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className="input-affix-btn"
              onClick={() => setReveal((value) => !value)}
              aria-label={reveal ? 'Hide password' : 'Show password'}
              aria-pressed={reveal}
            >
              {reveal ? <EyeOffIcon size={17} /> : <EyeIcon size={17} />}
            </button>
          </div>
          <span className="field-hint">At least 8 characters.</span>
        </div>
        <div className="field">
          <label htmlFor="confirm">Confirm new password</label>
          <input
            id="confirm"
            className="input"
            type={reveal ? 'text' : 'password'}
            value={confirm}
            autoComplete="new-password"
            required
            minLength={8}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? 'Saving…' : 'Save and sign in'}
        </button>
      </form>
      <p className="auth-switch">
        <Link to="/login">Back to sign in</Link>
      </p>
    </AuthShell>
  )
}

/** The two-column shell the other auth pages use, for visual consistency. */
function AuthShell({ headline, children }: { headline: string; children: React.ReactNode }) {
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
          <h1 className="auth-headline">{headline}</h1>
        </div>
      </aside>
      <main className="auth-panel">
        <div className="auth-card">{children}</div>
      </main>
    </div>
  )
}
