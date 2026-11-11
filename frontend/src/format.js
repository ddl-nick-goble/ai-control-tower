// "2026-11-07T02:14:00.000000Z" -> "2026-11-07 02:14 UTC"
export function fmtTs(ts) {
  if (!ts) return '-'
  const d = new Date(ts)
  if (isNaN(d)) return String(ts)
  return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
}

export function fmtJson(v) {
  if (v == null) return ''
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
}

const STATUS_LABELS = { pending_review: 'Pending review', discovered: 'Discovered', approved: 'Approved', shadow: 'Shadow' }
export function fmtStatus(s) {
  if (!s) return 'Unknown'
  if (STATUS_LABELS[s]) return STATUS_LABELS[s]
  const t = String(s).replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

// Never show provider/model ids from trace or finding data.
const MODEL_RE = /(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[.:/_-])*(?:claude|anthropic|sonnet|haiku|opus)[A-Za-z0-9]*(?:[.:/_-][A-Za-z0-9]+)*/gi
export function clean(v) {
  if (v == null) return v
  return String(v).replace(MODEL_RE, 'frontier model')
}

export function cleanText(v, max = 600) {
  const s = clean(fmtJson(v))
  return s.length > max ? s.slice(0, max) + '...' : s
}

// Message arrays ([{role, content}]) -> readable lines; anything else -> JSON.
export function fmtMessages(v) {
  if (v == null || v === '') return ''
  if (Array.isArray(v)) {
    return v.map((m) => (m && typeof m === 'object' && 'content' in m
      ? `${m.role ? m.role + ': ' : ''}${fmtJson(m.content)}` : fmtJson(m))).join('\n')
  }
  return fmtJson(v)
}

// One lookup for how a source platform code is shown. Codes stay the filter values and data attributes.
const SOURCE_LABELS = { aws: 'AWS AgentCore', google: 'Google', microsoft: 'Microsoft', databricks: 'Databricks', domino: 'Domino', external: 'External' }
export function sourceLabel(code) {
  if (!code) return 'Unknown'
  if (SOURCE_LABELS[code]) return SOURCE_LABELS[code]
  const t = String(code).replace(/[_-]+/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

// "2 min ago"; older than 30 days falls back to the UTC date.
export function relTime(ts, now = Date.now()) {
  if (!ts) return '-'
  const d = new Date(ts)
  if (isNaN(d)) return String(ts)
  const sec = Math.round((now - d.getTime()) / 1000)
  if (sec < 45) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} min ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr} ${hr === 1 ? 'hour' : 'hours'} ago`
  const day = Math.round(hr / 24)
  if (day <= 30) return `${day} ${day === 1 ? 'day' : 'days'} ago`
  return d.toISOString().slice(0, 10)
}

// The one place an agent page URL is built (ids contain ':'; keep them readable in the hash).
export function agentPath(id) {
  return `/agents/${encodeURIComponent(id).replace(/%3A/gi, ':')}`
}

// Numeric strings stay strings (no precision loss above 15 digits, trailing zeros kept); only the integer part gets separators.
// Missing values return null so callers can show a muted dash.
export function num(v) {
  if (v == null || v === '') return null
  const s = typeof v === 'string' ? v.trim() : typeof v === 'number' && Number.isFinite(v) ? String(v) : null
  if (s != null && /^-?(0|[1-9]\d*)(\.\d+)?$/.test(s)) {
    const [int, frac] = s.split('.')
    return int.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (frac ? `.${frac}` : '')
  }
  return clean(v)
}
