import React from 'react'
import { clean } from '../format'
import Markdown from './Markdown'
import { Panel } from './ui'

// Split the pack Markdown into the title (h3) and the seven sections (h3), each with a safe Markdown body.
function Preview({ markdown }) {
  const parts = []
  let cur = null
  for (const line of String(markdown || '').split(/\r?\n/)) {
    const m = /^(#{1,2})\s+(.*)/.exec(line)
    if (m) { cur = { level: m[1].length, title: m[2], body: [] }; parts.push(cur) }
    else if (cur) cur.body.push(line)
  }
  return (
    <div data-testid="evidence-pack-preview" className="evidence-preview">
      {parts.map((p, i) => (
        <section key={i} style={{ marginTop: i ? 16 : 0 }}>
          <h3 style={p.level === 1 ? { fontSize: 18 } : undefined}>{clean(unescapeText(p.title))}</h3>
          <Markdown text={p.body.join('\n')} />
        </section>
      ))}
    </div>
  )
}

// Headings are plain text here (not run through the Markdown renderer): undo the pack's escaping.
const unescapeText = (t) => t.replace(/\\([\\!\[\]()`*_#|&])/g, '$1').replace(/&lt;/g, '<').replace(/&gt;/g, '>')

function bundleNote(res) {
  const g = String(res.governance || '')
  if (!res.bundle_id) return 'No Governance bundle for this agent. Files are saved in the project dataset.'
  const where = `Recorded against Governance bundle ${clean(res.bundle_id)}.`
  if (g === 'attached') return `${where} Attached to the bundle as policy evidence.`
  return `${where} Attaching to the bundle failed; files are saved in the project dataset.`
}

export default function EvidencePack({ id }) {
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const [res, setRes] = React.useState(null)
  const generate = async () => {
    setBusy(true); setErr(null)
    try {
      const r = await fetch(`api/agents/${encodeURIComponent(id)}/evidence-pack`, { method: 'POST' })
      let body = null
      try { body = await r.json() } catch { /* non-JSON */ }
      if (!r.ok) {
        throw new Error(r.status === 401 ? 'Not signed in: sign in to Domino to generate a pack.'
          : (body && typeof body.detail === 'string' ? body.detail : r.statusText || 'Request failed'))
      }
      setRes(body)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  const base = res ? `api/evidence-packs/${res.pack_id}` : ''
  return (
    <Panel title="Evidence pack" testid="evidence-pack-card"
           aside={<button className="btn primary" data-testid="evidence-pack-generate" onClick={generate} disabled={busy}>
             {busy ? 'Generating...' : 'Generate evidence pack'}</button>}>
      {!res && !err && <p className="muted">Generate a dated pack in OCC 2026-54 order from ACT's own records.</p>}
      {err && <div className="alert error" role="alert">{clean(err)}</div>}
      {res && (
        <div data-testid="evidence-pack-result">
          <p>
            <a data-testid="evidence-pack-md-link" href={`${base}/markdown`} download>Download Markdown</a>{' · '}
            <a data-testid="evidence-pack-pdf-link" href={`${base}/pdf`} download>Download PDF</a>
          </p>
          <p data-testid="evidence-pack-bundle" className="muted">{bundleNote(res)}</p>
          <Preview markdown={res.markdown} />
        </div>
      )}
    </Panel>
  )
}
