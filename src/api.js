// Thin wrapper around the GhostTrack REST API.
//
// Authentication uses an httpOnly session cookie that the browser sends automatically with
// same-origin requests, so no token is ever handled in JavaScript. Any 401 fires a
// "ghosttrack:unauthorized" event and the app returns to the sign-in screen.

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, { credentials: 'same-origin', ...options })
  } catch (err) {
    if (err?.name === 'AbortError') throw err
    throw new ApiError('Cannot reach the GhostTrack API.', 0)
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body.detail) {
        detail = typeof body.detail === 'string'
          ? body.detail
          : body.detail.map?.((d) => d.msg).join('; ') || JSON.stringify(body.detail)
      }
    } catch { /* not JSON */ }
    if (res.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new Event('ghosttrack:unauthorized'))
    }
    throw new ApiError(detail, res.status)
  }
  return res.json()
}

const send = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export function qs(params) {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    sp.append(key, value)
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

const enc = encodeURIComponent

export const api = {
  health: () => request('/health'),
  me: () => request('/auth/me'),
  login: (username, password) => request('/auth/login', send('POST', { username, password })),
  logout: () => request('/auth/logout', { method: 'POST' }),

  live: (mode = 'recorded', region = 'socal', signal) => request(`/live${qs({ region, mode })}`, { signal }),
  aircraft: (icao, signal) => request(`/aircraft/${enc(icao)}`, { signal }),
  alerts: (params = {}) => request(`/alerts${qs(params)}`),
  alert: (id) => request(`/alerts/${enc(id)}`),
  detections: () => request('/detections'),
  scenarios: () => request('/scenarios'),
  /** Same-origin download links; the session cookie goes with them. */
  evidenceUrl: (id, ext) => `/api/alerts/${enc(id)}/evidence.${ext}`,
  reportUrl: (ext) => `/api/reports/summary.${ext}`,

  // Writer-only; the server answers 403 for guests.
  triage: (id, body) => request(`/alerts/${enc(id)}`, send('PATCH', body)),
}
