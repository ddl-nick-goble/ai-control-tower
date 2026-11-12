// Connections: the provider gallery (design spec 3.1). Six provider cards plus Other, each with its own state, and
// the Domino strip above them. Reads GET /api/connections; sample connections never look live.
import React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { agentsApi, connectionsApi } from '../api'
import { PageHeader, Button } from '../components/form'
import { ErrorPanel, ProviderMark, SampleBadge, StatusPill } from '../components/connect/bits'
import { PROVIDERS, PROVIDER_IDS } from '../components/connect/providers'
import { relTime } from '../format'

const STALE_MS = 24 * 3600 * 1000
const age = (c) => Date.now() - new Date(c.created_at).getTime()

// Which connection a card shows: an advanced live one, else a sample one, else a recent live draft.
export function cardModel(provider, conns) {
  const mine = conns.filter((c) => c.provider === provider && c.state !== 'disconnected')
  const live = mine.filter((c) => !c.sample)
  const advanced = live.find((c) => !['draft', 'waiting'].includes(c.state))
  const sample = mine.find((c) => c.sample)
  const draft = live.find((c) => ['draft', 'waiting'].includes(c.state) && age(c) < STALE_MS)
  if (advanced) return { kind: advanced.state, conn: advanced }
  if (sample) return { kind: 'sample', conn: sample, samples: mine.filter((c) => c.sample) }
  if (draft) return { kind: 'connecting', conn: draft }
  return { kind: 'none' }
}

function Meta({ rows }) {
  return <dl className="po-card-meta">{rows.map(([k, v]) => <div key={k}><dt>{k}:</dt><dd>{v}</dd></div>)}</dl>
}

function ProviderCard({ id, model, onChange }) {
  const p = PROVIDERS[id]
  const { kind, conn } = model
  const [busy, setBusy] = React.useState(false)
  const connect = `/connect/${id}`
  const access = conn ? `/connect/${id}/${conn.id}/access` : connect
  let status, meta, primary, secondary
  const agents = conn ? conn.agent_count : 0
  if (kind === 'none') {
    status = <StatusPill kind="idle" testid={`provider-card-${id}-status`}>Not connected</StatusPill>
    meta = [['Setup', p.setup], ['Needs', p.who]]
    primary = { label: `Connect ${p.name}`, to: connect, solid: true }
    secondary = <Link className="ds-btn ds-btn-ghost ds-btn-sm" to={`${connect}?sample=1`} data-testid={`provider-card-${id}-sample`} aria-describedby={`pc-${id}`}>Explore sample data</Link>
  } else if (kind === 'sample') {
    status = <span data-testid={`provider-card-${id}-status`}><SampleBadge /></span>
    meta = [['Source', `Fictional sample tenant, not read from ${p.name}`], ['Agents', agents ? `${agents} sample agents` : 'None imported yet']]
    primary = { label: `Connect real ${p.name}`, to: connect, solid: true }
    secondary = (
      <button type="button" className="ds-btn ds-btn-ghost ds-btn-sm" disabled={busy} data-testid={`provider-card-${id}-sample-remove`} aria-describedby={`pc-${id}`}
              onClick={async () => { setBusy(true); await Promise.allSettled(model.samples.map((s) => connectionsApi.remove(s.id))); setBusy(false); onChange() }}>Remove sample data</button>
    )
  } else if (kind === 'connecting') {
    status = <StatusPill kind="busy" testid={`provider-card-${id}-status`}>{id === 'databricks' ? 'Waiting for connection code' : `Waiting for ${p.name}`}</StatusPill>
    meta = [['Started', `${relTime(conn.created_at)}${conn.created_by ? ` by ${conn.created_by}` : ''}`], ['Next', id === 'databricks' ? 'Paste the code the setup script prints' : 'Finish the setup in your cloud console']]
    primary = { label: 'Resume setup', to: connect, solid: true }
    secondary = (
      <button type="button" className="ds-btn ds-btn-ghost ds-btn-sm" disabled={busy} data-testid={`provider-card-${id}-discard`} aria-describedby={`pc-${id}`}
              onClick={async () => { setBusy(true); try { await connectionsApi.remove(conn.id) } catch { /* shown again on reload */ } setBusy(false); onChange() }}>Discard setup</button>
    )
  } else if (kind === 'trusted') {
    status = <StatusPill kind="info" testid={`provider-card-${id}-status`}>Trust detected</StatusPill>
    meta = [['Started', `${relTime(conn.created_at)}${conn.created_by ? ` by ${conn.created_by}` : ''}`], ['Next', 'Check access, then choose scope']]
    primary = { label: 'Continue setup', to: access, solid: true }
  } else if (kind === 'needs_attention') {
    status = <StatusPill kind="warn" testid={`provider-card-${id}-status`}>Needs attention</StatusPill>
    meta = [['Problem', `${p.name} access checks need attention. Scans are paused.`], ['Last check', conn.last_check ? relTime(conn.last_check) : 'Not checked yet']]
    primary = { label: 'Fix connection', to: access, solid: true }
  } else if (kind === 'failed') {
    status = <StatusPill kind="err" testid={`provider-card-${id}-status`}>Setup failed</StatusPill>
    meta = [['Problem', `${p.name} didn't accept the trust.`]]
    primary = { label: 'Try again', to: connect, solid: true }
  } else { // verified | partial | connected
    status = <StatusPill kind="ok" testid={`provider-card-${id}-status`}>Connected · {agents} {agents === 1 ? 'agent' : 'agents'}</StatusPill>
    meta = [['Last scan', conn.last_scan ? relTime(conn.last_scan) : 'Not scanned yet'], ['Access', conn.last_check ? `${kind === 'partial' ? 'Optional checks missing · ' : 'Checked '}${relTime(conn.last_check)}` : 'Not checked yet']]
    primary = { label: 'Open connection', to: `/connect/${id}/${conn.id}/scope` }
  }
  return (
    <article className="ds-card po-card" data-testid={`provider-card-${id}`} aria-labelledby={`pc-${id}`}>
      <div className="po-card-head">
        <ProviderMark id={id} />
        <div className="po-card-titles"><h3 className="po-card-title" id={`pc-${id}`}>{p.name}</h3><p className="po-card-products">{p.products}</p></div>
      </div>
      <div>{status}</div>
      <Meta rows={meta} />
      <div className="po-card-actions">
        <Link className={`ds-btn ds-btn-sm${primary.solid ? ' ds-btn-primary' : ''}`} to={primary.to} data-testid={`provider-card-${id}-connect`} aria-describedby={`pc-${id}`}>{primary.label}</Link>
        {secondary}
      </div>
    </article>
  )
}

function OtherCard() {
  return (
    <article className="ds-card po-card po-card-other" data-testid="provider-card-other" aria-labelledby="pc-other">
      <div className="po-card-head">
        <ProviderMark id="other" />
        <div className="po-card-titles"><h3 className="po-card-title" id="pc-other">Other</h3><p className="po-card-products">Any agent that publishes an A2A agent card</p></div>
      </div>
      <Meta rows={[['Setup', 'Paste the agent card URL, one agent at a time'], ['Tier', 'You score seven questions']]} />
      <div className="po-card-actions">
        <Link className="ds-btn ds-btn-sm" to="/connections/add-by-url" data-testid="provider-card-other-connect" aria-describedby="pc-other">Add an agent by URL</Link>
      </div>
    </article>
  )
}

function DominoStrip() {
  const nav = useNavigate()
  const [state, setState] = React.useState({ loading: true, count: 0, error: false })
  React.useEffect(() => {
    let live = true
    agentsApi.list().then(
      (rows) => live && setState({ loading: false, count: (rows || []).filter((a) => a.source_platform === 'domino').length, error: false }),
      () => live && setState({ loading: false, count: 0, error: true }))
    return () => { live = false }
  }, [])
  const s = state
  return (
    <section className="ds-card po-domino" data-testid="domino-connection-card" aria-labelledby="dom-h">
      <ProviderMark id="domino" />
      <div className="po-domino-text">
        <h2 className="po-domino-title" id="dom-h">Domino (this deployment)</h2>
        <p className="ds-helper po-flat">{s.error ? "ACT can't read Domino projects right now. Try again in a moment." : "Connected automatically through ACT's own Domino identity. Nothing to set up."}</p>
      </div>
      {s.loading ? <StatusPill kind="idle" testid="domino-connection-status">Checking…</StatusPill>
        : s.error ? <StatusPill kind="warn" testid="domino-connection-status">Needs attention</StatusPill>
        : <StatusPill kind="ok" testid="domino-connection-status">Connected · {s.count} {s.count === 1 ? 'agent' : 'agents'}</StatusPill>}
      <Button className="ds-btn-sm" data-testid="domino-connection-open" onClick={() => nav('/')}>Open</Button>
    </section>
  )
}

function SkeletonCard({ i }) {
  return (
    <div className="ds-card po-card" aria-hidden="true" key={i}>
      <div className="po-card-head"><span className="po-mark po-mark-skel" /><div className="po-card-titles"><span className="ds-skel po-skel-title" /><span className="ds-skel po-skel-short" /></div></div>
      <span className="ds-skel po-skel-pill" /><span className="ds-skel" /><span className="ds-skel po-skel-short" />
    </div>
  )
}

export default function Connections() {
  const [data, setData] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [rev, setRev] = React.useState(0)
  React.useEffect(() => {
    let live = true
    setError(false)
    connectionsApi.list().then((r) => live && setData(r.connections || []), () => live && setError(true))
    return () => { live = false }
  }, [rev])
  const reload = () => { setData(null); setRev((r) => r + 1) }
  const loading = !data && !error
  return (
    <div className="ds-page ds-container po-page">
      <PageHeader title="Connections"
                  description="Connect the platforms where your agents run. ACT finds the agents, suggests a tier for each, and you choose what to import. ACT only reads: it never runs or changes an agent." />
      <DominoStrip />
      <div className="po-section-h">
        <h2 id="cloud-h">Cloud platforms</h2>
        <span className="ds-helper po-flat">Each setup uses a one-time trust: no passwords or keys are stored.</span>
      </div>
      <section aria-labelledby="cloud-h" data-testid="provider-gallery" aria-busy={loading ? 'true' : 'false'}>
        {error && (
          <ErrorPanel title="Connections didn't load" onRetry={reload}>ACT couldn't reach its database. Try again in a moment.</ErrorPanel>
        )}
        {loading && <div className="po-grid">{[0, 1, 2, 3, 4, 5].map((i) => <SkeletonCard key={i} i={i} />)}</div>}
        {data && (
          <div className="po-grid">
            {PROVIDER_IDS.map((id) => <ProviderCard key={id} id={id} model={cardModel(id, data)} onChange={reload} />)}
            <OtherCard />
          </div>
        )}
      </section>
    </div>
  )
}
