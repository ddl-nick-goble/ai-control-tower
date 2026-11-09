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

// Never show provider/model ids from trace or finding data.
const MODEL_RE = /[\w.:/-]*(?:claude|anthropic|sonnet|haiku|opus)[\w.:/-]*/gi
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
