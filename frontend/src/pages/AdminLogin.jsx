import { useState } from 'react'
import { adminLogin } from '../api/client.js'

export default function AdminLogin({ configured = true, onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event) {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('Enter both a username and a password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const data = await adminLogin(username.trim(), password)
      // Password is never stored anywhere — only the CSRF token is kept in memory.
      setPassword('')
      onSuccess(data.username, data.csrf_token)
    } catch (err) {
      setError(err.message || 'Login failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel narrow">
      <h2>Admin login</h2>
      <p className="hint">
        Administrator access is required to manage the doctor directory. Credentials are configured
        on the server through environment variables.
      </p>

      {!configured && (
        <p className="alert warn">
          The admin account is not configured on the server yet. Set
          CAREGRAPH_ADMIN_USERNAME and CAREGRAPH_ADMIN_PASSWORD_HASH (or
          CAREGRAPH_ADMIN_PASSWORD for local development) and restart the backend.
        </p>
      )}

      <form onSubmit={handleSubmit} className="stack">
        <label>
          Username
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      {error && <p className="alert error">{error}</p>}
    </section>
  )
}
