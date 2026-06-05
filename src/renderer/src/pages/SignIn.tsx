import { useState, useEffect } from 'react'
import type { AuthState } from '../App'

const WEB = 'https://renderfarm.swade-art.com'

function decodeUser(token: string): { id: string; email: string; isAdmin: boolean } {
  try {
    const p = JSON.parse(atob(token.split('.')[1]))
    return { id: String(p.sub ?? ''), email: String(p.email ?? ''), isAdmin: Boolean(p.isAdmin) }
  } catch {
    return { id: '', email: '', isAdmin: false }
  }
}

export default function SignInPage({ onLogin }: { onLogin: (a: AuthState) => void }) {
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const [googleLoading, setGoogleLoading] = useState(false)
  const [version,  setVersion]  = useState('')

  useEffect(() => {
    window.rfApi.app.version().then(setVersion).catch(() => setVersion(''))
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const result = await window.rfApi.auth.login(email, password)
      onLogin({ token: result.access_token, user: result.user })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleGoogle = async () => {
    setGoogleLoading(true)
    setError('')
    try {
      const { token, email: gEmail } = await window.rfApi.auth.browserLogin()
      const user = decodeUser(token)
      onLogin({ token, user: { ...user, email: user.email || gEmail } })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Google sign-in failed'
      if (!/timed out/i.test(msg)) setError(msg)
    } finally {
      setGoogleLoading(false)
    }
  }

  const open = (path: string) => window.rfApi.shell.open(`${WEB}${path}`)

  return (
    <div className="signin-shell">
      <div className="signin-card">
        {/* Logo */}
        <div className="signin-logo">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="2">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
          </svg>
          <span className="signin-brand">Renderfarm</span>
        </div>

        {/* Lock icon */}
        <div className="signin-lock">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="2"
               strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
        </div>

        <h1 className="signin-title">Sign in to your account</h1>
        <p className="signin-sub">Connect to the render farm service</p>

        {error && <div className="signin-error">✗ {error}</div>}

        <form onSubmit={handleSubmit} className="signin-form">
          <div className="signin-field">
            <label htmlFor="email">Email</label>
            <input
              id="email" type="email" value={email} autoComplete="email"
              onChange={(e) => setEmail(e.target.value)}
              className="signin-input" required
            />
          </div>
          <div className="signin-field">
            <label htmlFor="password">Password</label>
            <div className="signin-pw-wrap">
              <input
                id="password" type={showPw ? 'text' : 'password'} value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
                className="signin-input" required
              />
              <button type="button" className="signin-pw-toggle"
                onClick={() => setShowPw(v => !v)} aria-label="Toggle password visibility">
                {showPw ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>
          <button type="submit" disabled={loading} className="signin-btn">
            {loading ? <><span className="signin-spinner" /> Signing in…</> : 'Sign In'}
          </button>
        </form>

        <button type="button" className="signin-link-btn" onClick={() => open('/forgot-password')}>
          Forgot password?
        </button>

        <div className="signin-divider">
          <span className="signin-divider-line" />
          <span className="signin-divider-text">Or</span>
          <span className="signin-divider-line" />
        </div>

        <button type="button" className="signin-google" onClick={handleGoogle} disabled={googleLoading}>
          <span className="signin-google-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
          </span>
          <span className="signin-google-text">
            {googleLoading ? 'Opening browser…' : 'Sign in with Google'}
          </span>
        </button>

        <p className="signin-create">
          No account?{' '}
          <button type="button" className="signin-create-link" onClick={() => open('/register')}>
            Create a New Account
          </button>
        </p>

        <p className="signin-footer">Renderfarm Companion{version ? ` · v${version}` : ''}</p>
        <p className="signin-legal">
          <button type="button" className="signin-legal-link" onClick={() => open('/privacy')}>Privacy Policy</button>
          {' · '}
          <button type="button" className="signin-legal-link" onClick={() => open('/terms')}>Terms of Service</button>
        </p>
      </div>
    </div>
  )
}
