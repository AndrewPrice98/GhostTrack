import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api.js'
import { Empty, ErrorState, Icon, Loading, LogoMark, SessionContext, usePageTitle, useSession } from './components/ui.jsx'
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

const NAV_GROUPS = [
  ['Monitor', [['/', 'Live map', 'map'], ['/alerts', 'Alerts', 'alert'], ['/aircraft', 'Aircraft', 'plane']]],
  ['Analyse', [['/detections', 'Detections', 'detections'], ['/scenarios', 'Scenarios', 'scenarios'], ['/reports', 'Reports', 'reports']]],
]
const ADMIN_GROUP = ['Admin', [['/admin/upload', 'Upload', 'upload']]]
const SECTIONS = { alerts: 'Alerts', aircraft: 'Aircraft', scenarios: 'Scenarios', detections: 'Detections', reports: 'Reports' }

function Brand() {
  return (
    <NavLink to="/" className="brand" aria-label="GhostTrack, live map">
      <LogoMark size={26} />
      <span className="brand-name">GhostTrack</span>
    </NavLink>
  )
}

/** Where the person is: the section, and the item inside it on detail pages. */
function Crumbs() {
  const { pathname } = useLocation()
  const [first, rest] = pathname.split('/').filter(Boolean)
  if (!first) return <p className="crumbs">Live map</p>
  if (first === 'admin') return <p className="crumbs">Admin <span aria-hidden="true">/</span> Upload</p>
  const label = SECTIONS[first]
  if (!label) return <p className="crumbs">Not found</p>
  if (!rest) return <p className="crumbs">{label}</p>
  const item = first === 'alerts' ? `#${decodeURIComponent(rest)}` : first === 'aircraft' ? decodeURIComponent(rest).toUpperCase() : decodeURIComponent(rest)
  return (
    <p className="crumbs">
      <Link to={`/${first}`}>{label}</Link> <span aria-hidden="true">/</span> <span className="mono">{item}</span>
    </p>
  )
}

function AccountArea({ onLogout }) {
  const { user, guest, demo, authConfigured, signIn } = useSession()
  if (guest) {
    return authConfigured ? <button type="button" className="btn ghost small" onClick={signIn}>Sign in</button> : null
  }
  return (
    <div className="account">
      {demo && <span className="chip neutral" title="Hosted demo: recorded traffic with scheduled injected scenarios.">Demo</span>}
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
  const { version, guest, demo } = useSession()
  const groups = guest ? NAV_GROUPS : [...NAV_GROUPS, ADMIN_GROUP] // the recording upload is for the admin only
  const mainRef = useRef(null)
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()
  useFocusOnNavigate(mainRef)
  useEffect(() => { setOpen(false) }, [pathname])
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className={`sidebar${open ? ' open' : ''}`} id="sidebar">
        <div className="sidebar-top"><Brand /></div>
        <nav className="nav" aria-label="Main">
          {groups.map(([heading, items]) => (
            <div className="nav-group" key={heading}>
              <p className="nav-heading" id={`nav-${heading}`}>{heading}</p>
              <ul aria-labelledby={`nav-${heading}`}>
                {items.map(([to, label, icon]) => (
                  <li key={to}><NavLink to={to} end={to === '/'}><Icon name={icon} />{label}</NavLink></li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <p className="status-line"><span className="status-dot live" aria-hidden="true" />{demo || guest ? 'Demo, read-only' : 'Signed in'}</p>
          <p className="muted small">v{version}</p>
        </div>
      </aside>
      {open && <button type="button" className="scrim" aria-label="Close menu" onClick={() => setOpen(false)} />}
      <div className="main-col">
        <header className="topbar">
          <button type="button" className="btn ghost small icon-only menu-btn" aria-expanded={open} aria-controls="sidebar"
            onClick={() => setOpen((v) => !v)}>
            <Icon name={open ? 'close' : 'menu'} /><span className="sr-only">{open ? 'Close menu' : 'Open menu'}</span>
          </button>
          <span className="topbar-brand"><Brand /></span>
          <Crumbs />
          <div className="topbar-right">
            <a className="gh-link" href={GITHUB_URL} target="_blank" rel="noreferrer">
              <span className="gh-text">GitHub</span><Icon name="external" size={14} />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
            <AccountArea onLogout={onLogout} />
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
          <p>{DISCLAIMER} {ATTRIBUTION} Map outlines: Natural Earth (public domain).</p>
          <p>
            GhostTrack v{version} · Never transmits; analyses decoded data only ·{' '}
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">Source on GitHub<span className="sr-only"> (opens in a new tab)</span></a>
          </p>
        </footer>
      </div>
    </div>
  )
}

function App() {
  // checking -> (anon | user); 'unconfigured' when the server has neither guests nor an admin
  const [state, setState] = useState({ phase: 'checking' })

  const check = useCallback(async () => {
    try {
      const health = await api.health()
      const base = { version: health.version, demo: health.demo, authConfigured: health.auth_configured, maxUploadMb: health.max_upload_mb }
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
    authConfigured: state.authConfigured, maxUploadMb: state.maxUploadMb, signIn: state.authConfigured ? signIn : null,
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
