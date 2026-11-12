// Relative API paths only: keeps the Domino proxy prefix.
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status }
}

async function request(path, opts) {
  const res = await fetch('api/' + path, opts)
  let body = null
  try { body = await res.json() } catch { /* non-JSON body */ }
  if (!res.ok) {
    const detail = body && (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail))
    throw new ApiError(res.status, detail || res.statusText || 'Request failed')
  }
  return body
}

export const api = {
  agents: (params = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) q.set(k, v) })
    const s = q.toString()
    return request('agents' + (s ? '?' + s : ''))
  },
  agent: (id) => request(`agents/${encodeURIComponent(id)}`),
  findings: (id) => request(`agents/${encodeURIComponent(id)}/findings`),
  governance: (id) => request(`agents/${encodeURIComponent(id)}/governance`),
  validation: (id) => request(`agents/${encodeURIComponent(id)}/validation`),
  dependencies: (id) => request(`agents/${encodeURIComponent(id)}/dependencies`),
  approve: (id, body) => request(`agents/${encodeURIComponent(id)}/approve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  }),
  timeline: (runId) => request(`runs/${encodeURIComponent(runId)}/timeline`),
}

export const registerExternal = (body) => request('agents/external', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

// Demo mode (served only when ACT_DEMO_MODE=1 on the server).
let healthPromise = null
export const health = () => (healthPromise ||= request('health').catch(() => ({ demo_mode: false, stand_ins: [] })))
// Re-read /api/health after the demo switch changed (the shell listens and re-renders the banner).
export const refreshHealth = () => { healthPromise = null; window.dispatchEvent(new Event('act:health')) }

const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const settingsApi = {
  access: () => request('settings/access'),
  list: () => request('settings'),
  realAgentCount: () => request('settings/real-agent-count'),
  put: (name, value) => request(`settings/${encodeURIComponent(name)}`, json('PUT', { value })),
  del: (name) => request(`settings/${encodeURIComponent(name)}`, { method: 'DELETE' }),
}
export const standIn = (h, name) => ((h && h.stand_ins) || []).find((s) => s.name === name)
const post = (path) => request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
export const demoApi = {
  approve: (id) => post(`demo/approve/${encodeURIComponent(id)}`),
  run: (id) => post(`demo/run/${encodeURIComponent(id)}`),
  reset: () => post('demo/reset'),
}

export const previewTier = (criteria) => request('tiering/preview', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ criteria }),
})

// Connect a provider (PO-1 API).
const enc = encodeURIComponent
export const connectionsApi = {
  list: () => request('connections'),
  get: (id) => request(`connections/${enc(id)}`),
  create: (provider, sample = false) => request('connections', json('POST', { provider, sample })),
  remove: (id) => request(`connections/${enc(id)}`, { method: 'DELETE' }),
  paste: (id, text) => request(`connections/${enc(id)}/paste`, json('POST', { text })),
  check: (id) => post(`connections/${enc(id)}/check`),
  scopes: (id) => request(`connections/${enc(id)}/scopes`),
  saveScopes: (id, selected) => request(`connections/${enc(id)}/scopes`, json('PUT', { selected })),
  // The return link lands on the SPA; its query string (state plus any role/project values) goes to the API as is.
  scans: (id) => request(`connections/${enc(id)}/scans`),
  startScan: (id) => post(`connections/${enc(id)}/scans`),
  awaiting: (id) => request(`connections/${enc(id)}/awaiting`),
  schedule: (id) => request(`connections/${enc(id)}/schedule`),
  saveSchedule: (id, schedule, time = '02:00') => request(`connections/${enc(id)}/schedule`, json('PUT', { schedule, time })),
  callback: (search) => request('connections/callback' + (search || '')),
}
export const agentsApi = { list: () => request('agents') }

// Scan, review and import (PO-4 API). Lists come back as {rows}.
export const scansApi = {
  get: (id) => request(`scans/${enc(id)}`),
  staged: (id, cls) => request(`scans/${enc(id)}/staged${cls ? `?class=${enc(cls)}` : ''}`),
  patch: (rowId, body) => request(`staged/${enc(rowId)}`, json('PATCH', body)),
  dismiss: (rowId, reason) => request(`staged/${enc(rowId)}/dismiss`, json('POST', { reason })),
  importRows: (id, ids) => request(`scans/${enc(id)}/import`, json('POST', { ids })),
}
