import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { KeyIcon } from '../components/Icons'
import { PulseMark } from '../components/PulseLogo'
import { api } from '../lib/api'

/**
 * Ask for a reset link.
 *
 * The success panel is shown for any address, registered or not, because the
 * API answers the same way for both — otherwise this page would leak which
 * addresses have accounts.
 */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  // Only populated when the server has no mail transport configured, so the
  // flow is still walkable end to end in a demo deployment.
  const [devLink, setDevLink] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    document.title = 'Reset your password · Pulse'
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await api.forgotPassword(email)
      setDevLink(result.reset_url)
      setSent(true)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not send the reset link')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-wrap">
      <aside className="auth-aside">
        <Link to="/" className="brand" style={{ padding: 0 }}>
          <span className="brand-mark">
            <PulseMark />
          </span>
          <span className="brand-name">Pulse</span>
        </Link>
        <div>
          <h1 className="auth-headline">
            Locked out? <em>It happens.</em>
          </h1>
          <p className="auth-sub">
            Enter the email on your account and we will send you a link to choose a new password.
          </p>
        </div>
      </aside>

      <main className="auth-panel">
        <div className="auth-card">
          {sent ? (
            <>
              <h2>Check your email</h2>
              <p className="sub">
                If <strong>{email}</strong> is registered, a reset link is on its way. The link
                expires in 30 minutes and can be used once.
              </p>
              {devLink && (
                <div className="auth-dev-note">
                  <strong>No mail server is configured.</strong> Use this link to continue:
                  <Link className="auth-dev-link" to={new URL(devLink).pathname + new URL(devLink).search}>
                    Set a new password
                  </Link>
                </div>
              )}
              <p className="auth-switch">
                <Link to="/login">Back to sign in</Link>
              </p>
            </>
          ) : (
            <>
              <h2>Reset your password</h2>
              <p className="sub">We will email you a link to set a new one.</p>
              <form className="auth-form" onSubmit={submit}>
                {error && <div className="form-error">{error}</div>}
                <div className="field">
                  <label htmlFor="email">Email</label>
                  <input
                    id="email"
                    className="input"
                    type="email"
                    value={email}
                    autoComplete="email"
                    autoFocus
                    required
                    placeholder="you@example.com"
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </div>
                <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
                  <KeyIcon size={16} />
                  {busy ? 'Sending…' : 'Send reset link'}
                </button>
              </form>
              <p className="auth-switch">
                Remembered it? <Link to="/login">Back to sign in</Link>
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
