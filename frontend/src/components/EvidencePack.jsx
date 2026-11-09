import React from 'react'
import { clean } from '../format'
import Markdown from './Markdown'

// The safe Markdown renderer treats _x_ as emphasis: put snake_case identifiers in code spans.
const codeify = (t) => t.replace(/`[^`]*`|\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b/g, (m) => (m.startsWith('`') ? m : `\`${m}\``))

// Split the pack Markdown into real headings (h2 title, h3 sections) plus safe Markdown bodies.
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
        <section key={i}>
          {p.level === 1 ? <h2>{clean(p.title)}</h2> : <h3>{clean(p.title)}</h3>}
          <Markdown text={codeify(p.body.map(clean).join('\n'))} />
        </section>
      ))}
    </div>
  )
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
    <div id="evidence-pack" style={{ display: 'contents' }}>
      <button className="btn primary" data-testid="evidence-pack-generate" onClick={generate} disabled={busy}>
        {busy ? 'Generating...' : 'Generate evidence pack'}
      </button>
      {err && <div className="alert error" role="alert" style={{ flexBasis: '100%' }}>{clean(err)}</div>}
      {res && (
        <div className="panel" data-testid="evidence-pack-result" style={{ flexBasis: '100%', marginTop: 12 }}>
          <h2>Evidence pack</h2>
          <p>
            <a data-testid="evidence-pack-md-link" href={`${base}/markdown`} download>Download Markdown</a>{' · '}
            <a data-testid="evidence-pack-pdf-link" href={`${base}/pdf`} download>Download PDF</a>
          </p>
          <p data-testid="evidence-pack-bundle" className="muted">
            {res.bundle_id ? `Attached to Governance bundle ${clean(res.bundle_id)}` : 'Saved to project dataset (no Governance bundle for this agent)'}
            {res.governance && res.governance.startsWith('attach failed') ? ' (attachment failed; pack saved to the project dataset)' : ''}
          </p>
          <Preview markdown={res.markdown} />
        </div>
      )}
    </div>
  )
}
