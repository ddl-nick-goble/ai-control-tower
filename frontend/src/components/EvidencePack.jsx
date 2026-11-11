import React from 'react'
import { clean } from '../format'
import Markdown from './Markdown'
import { Card, InlineError, Skel } from './agentui'

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
    <div data-testid="evidence-pack-preview" className="evidence-preview ag-preview" tabIndex={0} role="region" aria-label="Evidence pack preview">
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

// Generation state lives in the page (the header button triggers it); the card shows the state and the pack.
export function useEvidencePack(id) {
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const [res, setRes] = React.useState(null)
  const [doneAt, setDoneAt] = React.useState(0)
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
      setDoneAt(Date.now())
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return { busy, err, res, generate, doneAt }
}

export default function EvidencePack({ pack }) {
  const { busy, err, res, generate, doneAt } = pack
  const ref = React.useRef(null)
  // The header button and this card can be far apart: bring the finished pack into view and move focus to it.
  React.useEffect(() => {
    if (!doneAt || !ref.current) return
    const el = ref.current
    const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ block: 'start', behavior: calm ? 'auto' : 'smooth' })
    el.focus({ preventScroll: true })
    // programmatic focus: keep a visible ring until the card loses focus
    el.setAttribute('data-landed', '')
    const clear = () => el.removeAttribute('data-landed')
    el.addEventListener('blur', clear, { once: true })
    return () => el.removeEventListener('blur', clear)
  }, [doneAt])
  const base = res ? `api/evidence-packs/${res.pack_id}` : ''
  return (
    <Card title="Evidence pack" testid="evidence-pack-card" cardRef={ref}
          actions={<span className="ds-sr" role="status" aria-live="polite">{res ? 'Evidence pack ready' : err ? 'Evidence pack failed' : ''}</span>}>
      {!res && !err && !busy && <p className="ag-note">A dated pack in OCC 2026-54 order, built from ACT's own records. Use Generate evidence pack above.</p>}
      {busy && <Skel what="evidence pack" lines={2} />}
      {err && <InlineError error={{ message: clean(err) }} onRetry={generate} />}
      {res && (
        <div data-testid="evidence-pack-result">
          <p className="ag-note">
            <a data-testid="evidence-pack-md-link" href={`${base}/markdown`} download>Download Markdown</a>{' · '}
            <a data-testid="evidence-pack-pdf-link" href={`${base}/pdf`} download>Download PDF</a>
          </p>
          <p data-testid="evidence-pack-bundle" className="ag-note ag-muted">{bundleNote(res)}</p>
          <Preview markdown={res.markdown} />
        </div>
      )}
    </Card>
  )
}
