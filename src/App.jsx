import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api.js'
import { Empty, ErrorState, Icon, Loading, SessionContext, usePageTitle, useSession } from './components/ui.jsx'
import { ATTRIBUTION, DISCLAIMER, GITHUB_URL } from './lib.js'
import Aircraft from './pages/Aircraft.jsx'
import AircraftDetail from './pages/AircraftDetail.jsx'
import AlertDetail from './pages/AlertDetail.jsx'
import Alerts from './pages/Alerts.jsx'
import Detections from './pages/Detections.jsx'
import LiveMap from './pages/LiveMap.jsx'
import Login from './pages/Login.jsx'
import Reports from './pages/Reports.jsx'
import ScenarioPlayer from './pages/ScenarioPlayer.jsx'
import Scenarios from './pages/Scenarios.jsx'
import Upload from './pages/Upload.jsx'

const NAV = [
  ['/', 'Live map'],
  ['/alerts', 'Alerts'],
  ['/aircraft', 'Aircraft'],
  ['/detections', 'Detections'],
  ['/scenarios', 'Scenarios'],
  ['/reports', 'Reports'],
]

function Brand() {
  return (
    <NavLink to="/" className="brand" aria-label="GhostTrack, live map">
      <span className="brand-mark" aria-hidden="true"><Icon name="radar" size={20} /></span>
      <span className="brand-text">
        <span className="brand-name">GhostTrack</span>
        <span className="brand-sub">ADS-B spoofing detector</span>
      </span>
    </NavLink>
  )
}

function AccountArea({ onLogout }) {
  const { user, guest, demo, authConfigured, signIn } = useSession()
  if (guest) {
    return (
      <div className="account">
        <span className="badge demo" title="Hosted demo: recorded traffic with scheduled injected scenarios. Browsing is read-only.">
          <Icon name="lock" size={13} /> Demo · read-only
        </span>
        {authConfigured && <button type="button" className="btn ghost small" onClick={signIn}>Sign in</button>}
      </div>
    )
  }
  return (
    <div className="account">
      {demo && <span className="badge demo" title="Hosted demo: recorded traffic with scheduled injected scenarios.">Demo</span>}
      <span className="account-user">{user}</span>
      <button type="button" className="btn ghost small" onClick={onLogout}>Sign out</button>
    </div>
  )
}

/** Move focus to the page heading after navigation so screen readers announce the new page. */
function useFocusOnNavigate(mainRef) {
  const { pathname } = useLocation()
  const previous = useRef(pathname)
  useEffect(() => {
    if (previous.current === pathname) return undefined
    previous.current = pathname
    window.scrollTo(0, 0)
    let frame = 0
    const started = performance.now()
    const tryFocus = () => {
      const heading = mainRef.current?.querySelector('h1')
      if (heading) heading.focus({ preventScroll: true })
      else if (performance.now() - started < 3000) frame = requestAnimationFrame(tryFocus)
    }
    frame = requestAnimationFrame(tryFocus)
    return () => cancelAnimationFrame(frame)
  }, [pathname, mainRef])
}

function NotFound() {
  usePageTitle('Not found')
  return <div className="page"><Empty><h1 tabIndex={-1} className="h-inline">Page not found.</h1> <NavLink to="/">Back to the live map</NavLink></Empty></div>
}

function Shell({ onLogout }) {
  const { version, guest } = useSession()
  // The recording upload is for the admin only; guests never see the link.
  const nav = guest ? NAV : [...NAV, ['/admin/upload', 'Upload']]
  const mainRef = useRef(null)
  useFocusOnNavigate(mainRef)
  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <div className="topbar-inner">
          <Brand />
          <nav className="nav" aria-label="Main">
            <ul>
              {nav.map(([to, label]) => (
                <li key={to}><NavLink to={to} end={to === '/'}>{label}</NavLink></li>
              ))}
            </ul>
          </nav>
          <div className="topbar-right">
            <a className="gh-link" href={GITHUB_URL} target="_blank" rel="noreferrer">
              <Icon name="github" size={18} /><span className="gh-text">GitHub</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
            <AccountArea onLogout={onLogout} />
          </div>
        </div>
      </header>
      <main id="main" className="main" ref={mainRef}>
        <Routes>
          <Route path="/" element={<LiveMap />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:alertId" element={<AlertDetail />} />
          <Route path="/aircraft" element={<Aircraft />} />
          <Route path="/aircraft/:icao" element={<AircraftDetail />} />
          <Route path="/detections" element={<Detections />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/scenarios/:name" element={<ScenarioPlayer />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/admin/upload" element={<Upload />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="footer">
        <div className="footer-inner">
          <p className="disclaimer">{DISCLAIMER}</p>
          <p className="footer-meta">
            {ATTRIBUTION} Map outlines: Natural Earth (public domain).
          </p>
          <p className="footer-meta">
            GhostTrack v{version} · Never transmits; analyses decoded data only ·{' '}
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">Source on GitHub<span className="sr-only"> (opens in a new tab)</span></a>
          </p>
        </div>
      </footer>
    </div>
  )
}

function App() {
  // checking -> (anon | user); 'unconfigured' when the server has neither guests nor an admin
  const [state, setState] = useState({ phase: 'checking' })

  const check = useCallback(async () => {
    try {
      const health = await api.health()
      const base = { version: health.version, demo: health.demo, authConfigured: health.auth_configured }
      if (!health.guest && !health.auth_configured) return setState({ phase: 'unconfigured' })
      let me
      try {
        me = await api.me()
      } catch (err) {
        if (err.status === 401) return setState({ phase: 'anon', ...base })
        throw err
      }
      return setState({ phase: 'user', user: me.username, guest: me.guest, ...base })
    } catch (error) {
      return setState({ phase: 'error', error })
    }
  }, [])

  useEffect(() => { check() }, [check])

  // Any API call that comes back 401 (session expired) returns to the sign-in screen.
  useEffect(() => {
    const expire = () => setState((s) => (s.phase === 'user'
      ? { ...s, phase: 'anon', notice: 'Your session has expired. Sign in again.' }
      : s))
    window.addEventListener('ghosttrack:unauthorized', expire)
    return () => window.removeEventListener('ghosttrack:unauthorized', expire)
  }, [])

  const signIn = useCallback(() => setState((s) => ({ ...s, phase: 'anon', canCancel: true, notice: undefined })), [])

  const session = useMemo(() => (state.phase === 'user' ? {
    user: state.user, guest: state.guest, demo: state.demo, version: state.version,
    authConfigured: state.authConfigured, signIn: state.authConfigured ? signIn : null,
  } : null), [state, signIn])

  async function logout() {
    try { await api.logout() } catch { /* clearing local state is enough */ }
    await check() // in demo mode this lands back on the guest view
  }

  if (state.phase === 'checking') return <div className="center-wrap"><Loading label="Connecting…" /></div>
  if (state.phase === 'error') {
    return <div className="center-wrap"><ErrorState error={state.error} onRetry={check} /></div>
  }
  if (state.phase === 'unconfigured') {
    return (
      <div className="center-wrap">
        <div className="state error" role="alert">
          No admin account is configured on the server. Run <code>python -m ghosttrack create-admin</code> (or set
          the GHOSTTRACK_* environment variables on your host) and restart the API.
        </div>
      </div>
    )
  }
  if (state.phase === 'anon') {
    return (
      <Login notice={state.notice} onLogin={check}
        onCancel={state.canCancel ? () => setState((s) => ({ ...s, phase: 'user', canCancel: false })) : undefined} />
    )
  }
  return (
    <SessionContext.Provider value={session}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Shell onLogout={logout} />
      </BrowserRouter>
    </SessionContext.Provider>
  )
}

export default App
