import { useState } from 'react'
import { patientLogin, patientRegister, patientSessionFrom } from '../api/client.js'

// Shared sign-in / registration form. Used by the appointments page and by the
// booking modal when a patient tries to book while signed out. It only ever
// receives the CSRF token — the password is never stored in state after login.
export default function PatientAuth({ onSuccess, note }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ username: '', password: '', full_name: '', email: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [errors, setErrors] = useState([])

  function update(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function switchMode(next) {
    setMode(next)
    setError(null)
    setErrors([])
  }

  async function handleSubmit(event) {
    event.preventDefault()
    const username = form.username.trim()
    if (!username || !form.password) {
      setError('Enter both a username and a password.')
      return
    }
    setBusy(true)
    setError(null)
    setErrors([])
    try {
      if (mode === 'register') {
        await patientRegister({
          username,
          password: form.password,
          full_name: form.full_name.trim(),
          email: form.email.trim(),
        })
      }
      const data = await patientLogin(username, form.password)
      setForm({ username: '', password: '', full_name: '', email: '' })
      onSuccess(patientSessionFrom(data))
    } catch (err) {
      const details = err.payload?.errors
      if (Array.isArray(details) && details.length) setErrors(details)
      setError(err.message || 'Could not sign in.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel narrow">
      <h2>{mode === 'login' ? 'Patient sign in' : 'Create a patient account'}</h2>
      {note && <p className="hint">{note}</p>}

      <div className="row">
        <button
          type="button"
          className={mode === 'login' ? 'chip-button active' : 'chip-button'}
          onClick={() => switchMode('login')}
        >
          Sign in
        </button>
        <button
          type="button"
          className={mode === 'register' ? 'chip-button active' : 'chip-button'}
          onClick={() => switchMode('register')}
        >
          Register
        </button>
      </div>

      <form onSubmit={handleSubmit} className="stack">
        <label>
          Username
          <input
            type="text"
            autoComplete="username"
            value={form.username}
            onChange={(e) => update('username', e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={form.password}
            onChange={(e) => update('password', e.target.value)}
          />
        </label>
        {mode === 'register' && (
          <>
            <label>
              Full name (optional)
              <input
                type="text"
                autoComplete="name"
                value={form.full_name}
                onChange={(e) => update('full_name', e.target.value)}
              />
            </label>
            <label>
              Email (optional)
              <input
                type="email"
                autoComplete="email"
                value={form.email}
                onChange={(e) => update('email', e.target.value)}
              />
            </label>
          </>
        )}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account and sign in'}
        </button>
      </form>

      {errors.length > 0 && (
        <div className="alert error">
          <ul>
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="alert error">{error}</p>}
    </section>
  )
}
