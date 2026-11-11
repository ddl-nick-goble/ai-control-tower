import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, demoApi, standIn } from '../api'
import { clean, fmtStatus, fmtTs } from '../format'
import Passport from '../components/Passport'
import Markdown from '../components/Markdown'
import { Breadcrumb } from '../components/form'
import EvidencePack from '../components/EvidencePack'
import { ErrorBox, Loading, Panel, StatusPill, TierBadge, useAsync, useHealth } from '../components/ui'

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

function wrapId(s) {
  return String(s).split(/([/:@#?&=._-])/).map((part, i) => (i % 2 ? <React.Fragment key={i}>{part}<wbr /></React.Fragment> : part))
}

export function Identity({ agent }) {
  const id = agent.identity || {}
  const asserted = id.type === 'a2a-card'
  const sigText = { signed: 'signed and verified', unsigned: 'unsigned', invalid: 'signature invalid' }[agent.card_signature] || 'unsigned'
  const noDistinct = !asserted && (!agent.identity || !id.type || id.type === 'none' || id.distinct === false)
  return (
    <Panel title="Identity binding" testid="identity-panel">
      {asserted && (
        <div className={`alert asserted${agent.card_signature === 'signed' ? ' verified' : ''}`} data-testid="identity-card-asserted">Identity asserted by the agent card ({sigText})</div>
      )}
      {noDistinct && (
        <div className="alert error" data-testid="identity-flag-no-distinct">No distinct identity: this agent acts under a shared or borrowed credential.</div>
      )}
      {id.simulated && <p className="muted" data-testid="identity-simulated"><span className="chip chip-demo">{id.label || 'simulated service account (demo)'}</span></p>}
      <dl className="kv">
        <dt>Type</dt><dd>{id.type || '-'}</dd>
        <dt>Issuer</dt><dd>{id.issuer || '-'}</dd>
        <dt>Principal id</dt><dd className="wrap-id">{wrapId(id.principal || id.subject || '-')}</dd>
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
  const classifier = standIn(useHealth(), 'classifier')
  return (
    <Panel title="Dependencies">
      {error && <ErrorBox error={error} />}
      {loading && <Loading what="dependencies" />}
      {data && (
        <div>
          <h3>Models</h3>
          <ul className="plain">
            {data.models.map((m, i) => (
              <li key={i} data-testid="dependency-model">{clean(m.name)} <span className="chip">registry version {m.registry_version ?? '-'}{m.alias === 'champion' ? ' · champion' : ''}</span>{classifier && m.name === 'break_classifier' && <> <span className="chip chip-demo" data-testid="demo-classifier-label">{classifier.label}</span></>}</li>
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

function pct(x) { return x == null ? '-' : `${(Number(x) * 100).toFixed(1).replace(/\.0$/, '')}%` }
const f3 = (x) => (x == null ? '-' : Number(x).toFixed(3))

function Validation({ id }) {
  const { data, error, loading } = useAsync(
    () => api.validation(id).catch((e) => { if (e.status === 404) return null; throw e }), [id])
  const d = (data && data.detail) || {}
  const judge = d.judge_model_id === 'mock-llm' ? 'mock judge (not a model)' : (d.judge_alias ? `judge ${clean(d.judge_alias)}` : 'judge on file')
  return (
    <Panel title="Validation" testid="validation-card">
      {error && <ErrorBox error={error} />}
      {loading && <Loading what="validation" />}
      {!loading && !error && !data && <p className="muted">No validation record</p>}
      {data && (
        <dl className="kv">
          <dt>Mean</dt><dd>{f3(d.mean)}</dd>
          <dt>p5</dt><dd>{f3(d.p5)}</dd>
          <dt>p95</dt><dd>{f3(d.p95)}</dd>
          <dt>Pass rate</dt><dd>{pct(d.pass_rate)}</dd>
          <dt>Sample</dt><dd>{d.n_prompts} prompts x {d.reps} repetitions</dd>
          <dt>Judge</dt><dd>{judge}</dd>
          <dt>Errors / invalid</dt><dd>{d.error_count ?? 0} errors, {d.invalid_count ?? 0} invalid judge replies</dd>
        </dl>
      )}
    </Panel>
  )
}

function Governance({ gov }) {
  const sim = standIn(useHealth(), 'governance')
  return (
    <Panel title="Governance bundle">
      {sim && <p className="muted" data-testid="demo-governance-label"><span className="chip chip-demo">{sim.label}</span></p>}
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
  const demo = useHealth()
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const [ok, setOk] = React.useState(null)
  const approve = async (asDemo) => {
    setBusy(true); setErr(null); setOk(null)
    try {
      const r = await (asDemo === true ? demoApi.approve(id) : api.approve(id))
      setOk(`Approved${asDemo === true ? ' by the simulated demo approver' : ''}. Expires ${fmtTs(r.expires_at)}.`)
      onChanged()
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  return (
    <Panel title="Approvals" testid="approvals-panel" className="wide"
           aside={<span>
             <button className="btn primary" onClick={() => approve(false)} disabled={busy}>Approve</button>
             {demo && demo.demo_mode && <> <button className="btn" onClick={() => approve(true)} disabled={busy}>Approve as demo approver</button></>}
           </span>}>
      {err && <div className="alert error" role="alert" data-testid="approve-error">{approveError(err)}</div>}
      {ok && <div className="alert ok" role="status" data-testid="approve-success">{ok}</div>}
      {gov && gov.approvals.length ? (
        <table className="table compact"><thead><tr><th>Approver</th><th>Decision</th><th>When</th><th>Expires</th></tr></thead>
          <tbody>{gov.approvals.map((a) => (
            <tr key={a.id}><td>{clean(a.approver)}{/simulated/i.test(a.approver || '') && <> <span className="chip chip-demo">simulated</span></>}</td><td>{clean(a.decision)}</td><td className="nowrap">{fmtTs(a.created_at)}</td><td className="nowrap">{fmtTs(a.expires_at)}</td></tr>
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
              <span className="pill">{fmtStatus(f.status)}</span>
              <strong>{clean(f.tool)}</strong>
              <span className="muted">{fmtTs(f.ts)}</span>
            </div>
            <Markdown text={clean(f.explanation)} />
            <div className="f-foot">
            {f.explanation && (!(f.detail && f.detail.explanation_model) || f.detail.explanation_model === 'mock-llm') && (
              <span className="muted" data-testid="finding-explanation-source">
                {f.detail && f.detail.explanation_model ? 'mock text (not a model)' : 'template text (no model)'}
              </span>
            )}
            {f.run_id && <Link data-testid="finding-timeline-link" to={`/runs/${encodeURIComponent(f.run_id)}`}>View run timeline</Link>}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function Runs({ id, agentName }) {
  const demo = useHealth()
  const [n, setN] = React.useState(0)
  const [busy, setBusy] = React.useState(false)
  const [runErr, setRunErr] = React.useState(null)
  const runDemo = async () => {
    setBusy(true); setRunErr(null)
    try { await demoApi.run(id); setN((x) => x + 1) } catch (e) { setRunErr(e.message) } finally { setBusy(false) }
  }
  const runs = useAsync(() => fetch(`api/agents/${encodeURIComponent(id)}/runs`).then((r) => {
    if (!r.ok) throw new Error(`${r.status}: could not load runs`)
    return r.json()
  }), [id, n])
  const list = runs.data || []
  return (
    <Panel title="Runs" aside={demo && demo.demo_mode && agentName === 'recon-agent'
      ? <button className="btn" onClick={runDemo} disabled={busy}>Run recon agent</button> : null}>
      {runErr && <div className="alert error" role="alert">{runErr}</div>}
      {runs.error && <ErrorBox error={runs.error} />}
      {runs.loading && !runs.data && <Loading what="runs" />}
      {runs.data && !list.length && <p className="muted">No runs recorded.</p>}
      <ul className="plain">
        {list.map((r) => (
          <li key={r.run_id} data-testid="run-item">
            <Link to={`/runs/${encodeURIComponent(r.run_id)}`}>{clean(r.run_id)}</Link>{' '}
            <span className="muted">{fmtTs(r.started_at)} · {r.span_count} {r.span_count === 1 ? 'span' : 'spans'}</span>
          </li>
        ))}
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
      <Breadcrumb items={[{ label: 'Fleet', to: '/' }, { label: clean(a.name) }]} />
      <div className="page-head" style={{ flexWrap: 'wrap' }}>
        <h1>{clean(a.name)}</h1>
      </div>
      <p className="muted"><span className="chip">{a.source_platform}</span> owner {clean(a.owner) || '-'} · <StatusPill status={a.status} testid="agent-status" /> · last seen {fmtTs(a.last_seen)}</p>
      {a.origin === 'external' && <Passport agent={a} />}
      <div className="grid">
        <TierCard agent={a} />
        <Identity agent={a} />
        <Manifest agent={a} />
        <Dependencies id={id} />
        <Validation id={id} />
        {gov.data ? <Governance gov={gov.data} /> : <Panel title="Governance bundle">{gov.error ? <ErrorBox error={gov.error} /> : <Loading what="governance" />}</Panel>}
        <Approvals id={id} gov={gov.data} onChanged={() => setRev((r) => r + 1)} />
      </div>
      {fnd.error ? <ErrorBox error={fnd.error} /> : <Findings findings={fnd.data || []} />}
      <div id="evidence-pack-slot" data-slot="evidence-pack"><EvidencePack id={id} /></div>
      <Runs id={id} agentName={a.name} />
    </div>
  )
}
