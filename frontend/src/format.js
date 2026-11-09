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
