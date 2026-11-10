import { useState } from 'react'
import { api } from '../api.js'
import { Icon, usePageTitle } from '../components/ui.jsx'
import { DISCLAIMER } from '../lib.js'

export default function Login({ onLogin, notice, onCancel }) {
  usePageTitle('Sign in')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await api.login(username.trim(), password)
      setPassword('')
      await onLogin()
    } catch (err) {
      setError(err.status === 429 ? 'Too many failed attempts. Wait a few minutes and try again.' : err.message)
      setBusy(false)
    }
  }

  return (
    <main className="login-wrap">
      <form className="card login" onSubmit={submit} aria-labelledby="login-title">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true"><Icon name="radar" size={22} /></span>
          <span className="brand-name">GhostTrack</span>
        </div>
        <h1 id="login-title">Sign in</h1>
        <p className="muted">The admin can triage alerts and upload recordings. Everyone else can browse read-only.</p>
        {notice && <div className="state" role="status">{notice}</div>}
        <label className="field">
          <span>Username</span>
          <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)}
            maxLength={128} required autoFocus />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} maxLength={1024} required />
        </label>
        {error && <div className="state error" role="alert">{error}</div>}
        <button className="btn primary block" aria-disabled={busy ? 'true' : undefined}>{busy ? 'Signing in…' : 'Sign in'}</button>
        {onCancel && (
          <button type="button" className="btn ghost block" onClick={onCancel}>Back to the read-only demo</button>
        )}
        <p className="disclaimer">{DISCLAIMER}</p>
      </form>
    </main>
  )
}
