import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { clean, fmtTs } from '../format'
import Markdown from '../components/Markdown'
import EvidencePack from '../components/EvidencePack'
import { ErrorBox, Loading, Panel, StatusPill, TierBadge, useAsync } from '../components/ui'

const CRITERIA = [
  ['decision_impact', 'Decision impact'], ['financial_exposure', 'Financial exposure'],
  ['customer_impact', 'Customer impact'], ['autonomy', 'Autonomy'],
  ['data_sensitivity', 'Data sensitivity'], ['reversibility', 'Reversibility'],
  ['regulatory_exposure', 'Regulatory exposure'],
]

function TierCard({ agent }) {
  const c = agent.criteria || {}
  return (
    <Panel title="Tier and criteria" testid="tier-card">
      <p><TierBadge tier={agent.tier} /> <span className="muted">OCC Bulletin 2026-54, seven criteria scored 1-5</span></p>
      <ul className="criteria">
        {CRITERIA.map(([k, name]) => (
          <li key={k} data-testid="criteria-item"><span>{name}</span><strong>{c[k] ?? 'n/a'}</strong></li>
        ))}
      </ul>
      <div data-testid="tier-reasoning" className="reasoning">{agent.tier_reasoning || 'No reasoning recorded.'}</div>
    </Panel>
  )
}

function Identity({ agent }) {
  const id = agent.identity || {}
  const noDistinct = !agent.identity || !id.type || id.type === 'none' || id.distinct === false
  return (
    <Panel title="Identity binding" testid="identity-panel">
      {noDistinct && (
        <div className="alert error" data-testid="identity-flag-no-distinct">No distinct identity: this agent acts under a shared or borrowed credential.</div>
      )}
      <dl className="kv">
        <dt>Type</dt><dd>{id.type || '-'}</dd>
        <dt>Issuer</dt><dd>{id.issuer || '-'}</dd>
        <dt>Principal id</dt><dd>{id.principal || id.subject || '-'}</dd>
      </dl>
    </Panel>
  )
}

function Manifest({ agent }) {
  const m = agent.manifest || {}
  const limits = m.value_limits_detail || m.value_limits || {}
  const list = (xs) => (xs && xs.length ? xs.map(clean).join(', ') : 'none')
  return (
    <Panel title="Permitted-action manifest">
      <h3>Tools</h3>
      <ul className="tags tool-chips">
        {(m.tools || []).map((t) => <li key={t} data-testid="manifest-tool" className="chip">{clean(t)}</li>)}
        {!(m.tools || []).length && <li className="muted">none declared</li>}
      </ul>
      <dl className="kv">
        <dt>Data scopes</dt><dd>{list(m.data_scopes)}</dd>
        <dt>Systems</dt><dd>{list(m.systems)}</dd>
        <dt>Value limits</dt>
        <dd>{Object.keys(limits).length ? Object.entries(limits).map(([t, v]) => (
          <div key={t}>{clean(t)}: {typeof v === 'object' ? `${v.amount} ${v.currency || ''}` : v}</div>)) : 'none'}</dd>
      </dl>
    </Panel>
  )
}

function Dependencies({ id }) {
  const { data, error, loading } = useAsync(() => api.dependencies(id), [id])
  return (
    <Panel title="Dependencies">
      {error && <ErrorBox error={error} />}
      {loading && <Loading what="dependencies" />}
      {data && (
        <div>
          <h3>Models</h3>
          <ul className="plain">
            {data.models.map((m, i) => (
              <li key={i} data-testid="dependency-model">{clean(m.name)} <span className="chip">registry version {m.registry_version ?? '-'}</span></li>
            ))}
            {!data.models.length && <li className="muted">none</li>}
          </ul>
          <h3>MCP servers</h3>
          <ul className="plain">
            {data.mcp_servers.map((m, i) => <li key={i} data-testid="dependency-mcp">{clean(m.name)}{m.target ? ` (${clean(m.target)})` : ''}</li>)}
            {!data.mcp_servers.length && <li className="muted">none</li>}
          </ul>
          <h3>Tools</h3>
          <ul className="plain">
            {data.tools.map((m, i) => <li key={i} data-testid="dependency-tool">{clean(m.name)}</li>)}
            {!data.tools.length && <li className="muted">none recorded as dependencies</li>}
          </ul>
        </div>
      )}
    </Panel>
  )
}

function Governance({ gov }) {
  return (
    <Panel title="Governance bundle">
      <dl className="kv">
        <dt>Bundle</dt><dd data-testid="bundle-id">{gov.bundle_id || 'not yet created'}</dd>
        <dt>Stage</dt><dd>{gov.stage || '-'}</dd>
      </dl>
      <h3>{gov.bundle_id ? 'Policies attached to this bundle' : 'Policies the bundle will carry'}</h3>
      <ul className="plain" data-testid={gov.bundle_id ? 'policies-attached' : 'policies-expected'}>
        {(gov.policies || []).map((p, i) => <li key={i} data-testid="policy-name">{clean(p.name)}</li>)}
      </ul>
    </Panel>
  )
}

function approveError(err) {
  if (err.status === 401) return 'Not signed in: sign in to Domino to approve this agent.'
  if (err.status === 403) return clean(err.message) || 'Refused: the user who registered this agent cannot approve it.'
  if (err.status === 409) return clean(err.message) || 'Not awaiting approval, or it has no registrant on record.'
  return `Error ${err.status || ''}: ${clean(err.message)}`
}

function Approvals({ id, gov, onChanged }) {
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const [ok, setOk] = React.useState(null)
  const approve = async () => {
    setBusy(true); setErr(null); setOk(null)
    try {
      const r = await api.approve(id)
      setOk(`Approved. Expires ${fmtTs(r.expires_at)}.`)
      onChanged()
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  return (
    <Panel title="Approvals" testid="approvals-panel" className="wide"
           aside={<button className="btn primary" onClick={approve} disabled={busy}>Approve</button>}>
      {err && <div className="alert error" role="alert" data-testid="approve-error">{approveError(err)}</div>}
      {ok && <div className="alert ok" role="status" data-testid="approve-success">{ok}</div>}
      {gov && gov.approvals.length ? (
        <table className="table compact"><thead><tr><th>Approver</th><th>Decision</th><th>When</th><th>Expires</th></tr></thead>
          <tbody>{gov.approvals.map((a) => (
            <tr key={a.id}><td>{clean(a.approver)}</td><td>{clean(a.decision)}</td><td className="nowrap">{fmtTs(a.created_at)}</td><td className="nowrap">{fmtTs(a.expires_at)}</td></tr>
          ))}</tbody></table>
      ) : <p className="muted">No approvals recorded.</p>}
    </Panel>
  )
}

function Findings({ findings }) {
  return (
    <Panel title="Findings">
      {!findings.length && <p className="muted">No findings.</p>}
      <ul className="plain findings">
        {findings.map((f) => (
          <li key={f.id} data-testid="finding-item" data-finding-type={f.type} data-status={f.status} className={`sev-${f.severity}`}>
            <div className="f-head">
              <span className="chip">{f.type}</span>
              <span className={`pill sev-${f.severity}`}>{f.severity}</span>
              <span className="pill">{f.status}</span>
              <strong>{clean(f.tool)}</strong>
              <span className="muted">{fmtTs(f.ts)}</span>
            </div>
            <Markdown text={clean(f.explanation)} />
            {f.run_id && <Link data-testid="finding-timeline-link" to={`/runs/${encodeURIComponent(f.run_id)}`}>View run timeline</Link>}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function Runs({ findings }) {
  // No runs-by-agent endpoint yet: list the runs referenced by this agent's findings.
  const ids = [...new Set(findings.map((f) => f.run_id).filter(Boolean))]
  return (
    <Panel title="Runs">
      {!ids.length && <p className="muted">No runs with findings.</p>}
      <ul className="plain">
        {ids.map((r) => <li key={r} data-testid="run-item"><Link to={`/runs/${encodeURIComponent(r)}`}>{r}</Link></li>)}
      </ul>
    </Panel>
  )
}

export default function Agent() {
  const { id } = useParams()
  return <AgentView key={id} id={id} />
}

function AgentView({ id }) {
  const [rev, setRev] = React.useState(0)
  const agent = useAsync(() => api.agent(id), [id, rev])
  const gov = useAsync(() => api.governance(id), [id, rev])
  const fnd = useAsync(() => api.findings(id), [id])
  if (agent.error) return <ErrorBox error={agent.error} />
  if (!agent.data) return <Loading what="agent" />
  const a = agent.data
  return (
    <div>
      <p><Link to="/">Fleet</Link> / {clean(a.name)}</p>
      <div className="page-head" style={{ flexWrap: 'wrap' }}>
        <h1>{clean(a.name)}</h1>
        {/* SLOT: "Generate evidence pack" button (added by a later task) */}
        <div id="evidence-pack-slot" data-slot="evidence-pack" style={{ display: 'contents' }}><EvidencePack id={id} /></div>
      </div>
      <p className="muted"><span className="chip">{a.source_platform}</span> owner {clean(a.owner) || '-'} · <StatusPill status={a.status} testid="agent-status" /> · last seen {fmtTs(a.last_seen)}</p>
      <div className="grid">
        <TierCard agent={a} />
        <Identity agent={a} />
        <Manifest agent={a} />
        <Dependencies id={id} />
        {gov.data ? <Governance gov={gov.data} /> : <Panel title="Governance bundle">{gov.error ? <ErrorBox error={gov.error} /> : <Loading what="governance" />}</Panel>}
        <Approvals id={id} gov={gov.data} onChanged={() => setRev((r) => r + 1)} />
      </div>
      {fnd.error ? <ErrorBox error={fnd.error} /> : <Findings findings={fnd.data || []} />}
      <Runs findings={fnd.data || []} />
    </div>
  )
}
