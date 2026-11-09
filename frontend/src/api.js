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
  dependencies: (id) => request(`agents/${encodeURIComponent(id)}/dependencies`),
  approve: (id, body) => request(`agents/${encodeURIComponent(id)}/approve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  }),
  timeline: (runId) => request(`runs/${encodeURIComponent(runId)}/timeline`),
}

export const registerExternal = (body) => request('agents/external', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})
