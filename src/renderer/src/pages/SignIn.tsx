import { useState } from 'react'
import type { AuthState } from '../App'

export default function SignInPage({ onLogin }: { onLogin: (a: AuthState) => void }) {
  const [email,    setEmail]    = useState('silasshaibu2@gmail.com')
  const [password, setPassword] = useState('password123')
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)

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

        <h1 className="signin-title">Sign in to your account</h1>
        <p className="signin-sub">Connect to the render farm service</p>

        {error && <div className="signin-error">{error}</div>}

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
            <input
              id="password" type="password" value={password} autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              className="signin-input" required
            />
          </div>
          <button type="submit" disabled={loading} className="signin-btn">
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        <p className="signin-footer">
          Renderfarm Companion · v1.0.1
        </p>
      </div>
    </div>
  )
}
