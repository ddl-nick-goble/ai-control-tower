import React from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, demoApi, standIn } from '../api'
import { clean, fmtStatus, fmtTs, num, sourceLabel } from '../format'
import Passport from '../components/Passport'
import Markdown from '../components/Markdown'
import { Breadcrumb } from '../components/form'
import EvidencePack, { useEvidencePack } from '../components/EvidencePack'
import { StatusPill, TierBadge, useAsync, useHealth } from '../components/ui'
import { SampleBadge } from '../components/connect/bits'
import { Arrow, Card, ChevronRight, CopyButton, Dl, Empty, ErrorPage, IdValue, InlineError, None, Skel } from '../components/agentui'

const CRITERIA = [
  ['decision_impact', 'Decision impact'], ['financial_exposure', 'Financial exposure'],
  ['customer_impact', 'Customer impact'], ['autonomy', 'Autonomy'],
  ['data_sensitivity', 'Data sensitivity'], ['reversibility', 'Reversibility'],
  ['regulatory_exposure', 'Regulatory exposure'],
]

const nf = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'))

function Bar({ value }) {
  const v = Number(value)
  return (
    <span className="ag-bar" aria-hidden="true">
      {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= v ? 'on' : ''} />)}
    </span>
  )
}

function TierCard({ agent }) {
  const c = agent.criteria || {}
  return (
    <Card title="Tier and criteria" testid="tier-card" className="ag-ord-3">
      <p className="ag-tierline"><TierBadge tier={agent.tier} /><span className="ag-muted">OCC Bulletin 2026-54</span></p>
      <ul className="ag-criteria">
        {CRITERIA.map(([k, name]) => (
          <li key={k} data-testid="criteria-item">
            <span>{name}</span><Bar value={c[k]} /><strong>{c[k] ?? 'n/a'}</strong>
          </li>
        ))}
      </ul>
      <details className="ag-details">
        <summary>How the score is computed</summary>
        <div data-testid="tier-reasoning" className="ag-caption">{agent.tier_reasoning || 'No reasoning recorded.'}</div>
      </details>
    </Card>
  )
}

export function Identity({ agent }) {
  const id = agent.identity || {}
  const asserted = id.type === 'a2a-card'
  const sigText = { signed: 'signed and verified', unsigned: 'unsigned', invalid: 'signature invalid' }[agent.card_signature] || 'unsigned'
  const noDistinct = !asserted && (!agent.identity || !id.type || id.type === 'none' || id.distinct === false)
  return (
    <Card title="Identity binding" testid="identity-panel" className="ag-ord-5">
      {asserted && (
        <div className={`ag-note-box${agent.card_signature === 'signed' ? ' ok' : ' warn'}`} data-testid="identity-card-asserted">Identity asserted by the agent card ({sigText})</div>
      )}
      {noDistinct && (
        <div className="ds-alert ds-alert-error ag-flag" data-testid="identity-flag-no-distinct">No distinct identity: this agent acts under a shared or borrowed credential.</div>
      )}
      {id.simulated && <p className="ag-note"><span className="ag-demo-chip" data-testid="identity-simulated">{id.label || 'simulated service account (demo)'}</span></p>}
      <Dl rows={[
        ['Type', id.type || <None>—</None>],
        ['Issuer', id.issuer || <None>—</None>],
        ['Principal', <IdValue key="p" value={id.principal || id.subject} label="principal" />],
      ]} />
    </Card>
  )
}

function Manifest({ agent }) {
  const m = agent.manifest || {}
  const limits = m.value_limits_detail || m.value_limits || {}
  const chips = (xs, tid) => (xs && xs.length
    ? <ul className="ag-chips">{xs.map((t) => <li key={t} data-testid={tid} className="ds-chip-tag ag-wrap">{clean(t)}</li>)}</ul>
    : <None>none declared</None>)
  const limitText = (v) => (v && typeof v === 'object' ? `${num(v.amount) ?? '\u2014'} ${v.currency || ''}`.trim() : (num(v) ?? '\u2014'))
  return (
    <Card title="Permitted actions" className="ag-ord-5">
      <Dl rows={[
        ['Tools', chips(m.tools, 'manifest-tool')],
        ['Data scopes', chips(m.data_scopes)],
        ['Systems', chips(m.systems)],
        ['Value limits', Object.keys(limits).length ? Object.entries(limits).map(([t, v]) => (
          <div key={t}>{clean(t)}: {limitText(v)}</div>)) : <None>none declared</None>],
      ]} />
    </Card>
  )
}

function Dependencies({ id }) {
  const [rev, setRev] = React.useState(0)
  const { data, error, loading } = useAsync(() => api.dependencies(id), [id, rev])
  const classifier = standIn(useHealth(), 'classifier')
  return (
    <Card title="Dependencies" className="ag-ord-5">
      {error && <InlineError error={error} what="dependencies" onRetry={() => setRev((r) => r + 1)} />}
      {loading && !data && !error && <Skel what="dependencies" lines={1} className="ag-skel-dependencies" />}
      {data && !data.models.length && !data.mcp_servers.length && !data.tools.length && <p className="ag-note"><None>None declared</None></p>}
      {data && (data.models.length > 0 || data.mcp_servers.length > 0 || data.tools.length > 0) && (
        <Dl rows={[
          ['Models', data.models.length ? (
            <ul className="ag-plain">{data.models.map((m, i) => (
              <li key={i} data-testid="dependency-model" className="ag-model"><span className="ds-chip-tag">{clean(m.name)}</span><span className="ag-muted ag-model-ver">registry version {m.registry_version ?? '—'}{m.alias === 'champion' ? ' · champion' : ''}</span>{classifier && m.name === 'break_classifier' && <span className="ag-demo-chip" data-testid="demo-classifier-label">{classifier.label}</span>}</li>
            ))}</ul>) : <None />],
          ['MCP servers', data.mcp_servers.length ? (
            <ul className="ag-plain">{data.mcp_servers.map((m, i) => <li key={i} data-testid="dependency-mcp">{clean(m.name)}{m.target ? ` (${clean(m.target)})` : ''}</li>)}</ul>) : <None />],
          ['Tools', data.tools.length ? (
            <ul className="ag-chips">{data.tools.map((m, i) => <li key={i} data-testid="dependency-tool" className="ds-chip-tag">{clean(m.name)}</li>)}</ul>) : <None />],
        ]} />
      )}
    </Card>
  )
}

function pct(x) { return x == null ? '—' : `${(Number(x) * 100).toFixed(1).replace(/\.0$/, '')}%` }
const f3 = (x) => (x == null ? '—' : Number(x).toFixed(3))

function Validation({ id }) {
  const [rev, setRev] = React.useState(0)
  const { data, error, loading } = useAsync(
    () => api.validation(id).catch((e) => { if (e.status === 404) return null; throw e }), [id, rev])
  const d = (data && data.detail) || {}
  const judge = d.judge_model_id === 'mock-llm' ? 'mock judge (not a model)' : (d.judge_alias ? `judge ${clean(d.judge_alias)}` : 'judge on file')
  return (
    <Card title="Validation" testid="validation-card" className="ag-ord-5">
      {error && <InlineError error={error} what="validation" onRetry={() => setRev((r) => r + 1)} />}
      {loading && !data && !error && <Skel what="validation" lines={2} className="ag-skel-validation" />}
      {!loading && !error && !data && <Empty title="No validation record" hint="Validation scores appear here once the agent has been evaluated." />}
      {data && (
        <>
          <div className="ag-stats">
            {[['Mean', f3(d.mean)], ['p5', f3(d.p5)], ['p95', f3(d.p95)], ['Pass rate', pct(d.pass_rate)]].map(([k, v]) => (
              <div key={k}><span>{k}</span><strong>{v}</strong></div>
            ))}
          </div>
          <Dl rows={[
            ['Sample', `${nf(d.n_prompts)} prompts x ${nf(d.reps)} repetitions`],
            ['Judge', judge],
            ['Errors', `${d.error_count ?? 0} errors, ${d.invalid_count ?? 0} invalid judge replies`],
          ]} />
        </>
      )}
    </Card>
  )
}

function Governance({ gov, error, onRetry }) {
  const sim = standIn(useHealth(), 'governance')
  return (
    <Card title="Governance" className="ag-ord-5">
      {error && !gov && <InlineError error={error} what="governance" onRetry={onRetry} />}
      {!gov && !error && <Skel what="governance" lines={6} />}
      {gov && (
        <>
          {sim && <p className="ag-note"><span className="ag-demo-chip" data-testid="demo-governance-label">{sim.label}</span></p>}
          <Dl rows={[
            ['Bundle', gov.bundle_id ? <IdValue key="b" value={gov.bundle_id} label="bundle ID" testid="bundle-id" /> : <span data-testid="bundle-id">not yet created</span>],
            ['Stage', gov.stage || <None>—</None>],
          ]} />
          <p className="ag-label ag-label-gap">{gov.bundle_id ? 'Policies attached to this bundle' : 'Policies the bundle will carry'}</p>
          <ul className="ag-bullets" data-testid={gov.bundle_id ? 'policies-attached' : 'policies-expected'}>
            {(gov.policies || []).map((p, i) => <li key={i} data-testid="policy-name">{clean(p.name)}</li>)}
          </ul>
        </>
      )}
    </Card>
  )
}

function approveError(err) {
  if (err.status === 401) return 'Not signed in: sign in to Domino to approve this agent.'
  if (err.status === 403) return clean(err.message) || 'Refused: the user who registered this agent cannot approve it.'
  if (err.status === 409) return clean(err.message) || 'Not awaiting approval, or it has no registrant on record.'
  return `Error ${err.status || ''}: ${clean(err.message)}`
}

function Approvals({ gov, error, onRetry }) {
  return (
    <Card title="Approvals" testid="approvals-panel" className="ag-ord-5">
      {error && !gov && <InlineError error={error} what="approvals" onRetry={onRetry} />}
      {!gov && !error && <Skel what="approvals" lines={2} />}
      {gov && (gov.approvals.length ? (
        <table className="ds-table ag-table ag-table-static"><thead><tr><th scope="col">Approver</th><th scope="col">Decision</th><th scope="col">When</th><th scope="col">Expires</th></tr></thead>
          <tbody>{gov.approvals.map((a) => (
            <tr key={a.id}><td><span className="ag-wrap">{clean(a.approver)}</span>{/simulated/i.test(a.approver || '') && <> <span className="ag-demo-chip">simulated</span></>}</td><td>{clean(a.decision)}</td><td className="ds-nowrap">{fmtTs(a.created_at)}</td><td className="ds-nowrap">{fmtTs(a.expires_at)}</td></tr>
          ))}</tbody></table>
      ) : <Empty title="No approvals recorded." hint="Approval history appears here after a second person approves this agent." />)}
    </Card>
  )
}

function toolCounts(viol) {
  const m = new Map()
  for (const v of viol) { const t = clean(v.tool); if (t) m.set(t, (m.get(t) || 0) + 1) }
  return [...m].map(([t, n]) => (n > 1 ? `${t} \u00d7${n}` : t)).join(', ')
}

const SEV = ['critical', 'high', 'medium', 'low']

function Findings({ fnd, hl, onRetry }) {
  const { data, error, loading } = fnd
  const findings = data || []
  return (
    <Card title="Findings" className="ag-ord-2" actions={data ? <span className="ag-count">{nf(findings.length)}</span> : error ? <span className="ag-count" title="Count unavailable">–</span> : null}>
      {error && !data && <InlineError error={error} what="findings" onRetry={onRetry} />}
      {loading && !data && !error && <Skel what="findings" />}
      {data && !findings.length && <Empty title="No findings." hint="Scope drift and policy findings for this agent appear here." />}
      {findings.length > 0 && (
        <ul className="ag-findings">
          {findings.map((f) => {
            const spanId = f.span_id || (f.detail && f.detail.violations && f.detail.violations[0] && f.detail.violations[0].span_id) || ''
            const viol = (f.detail && f.detail.violations) || []
            const nViol = viol.length
            const sev = SEV.includes(f.severity) ? f.severity : 'low'
            const src = f.explanation && (!(f.detail && f.detail.explanation_model) || f.detail.explanation_model === 'mock-llm')
            return (
              <li key={f.id} id={`finding-${f.id}`} data-testid="finding-item" data-finding-type={f.type} data-status={f.status} className={`ag-finding sev-${sev}${hl === `finding-${f.id}` ? ' ag-flash' : ''}`}>
                <div className="ag-finding-main">
                  <div className="ag-finding-line">
                    <span className={`ag-sev ag-sev-${sev}`} data-testid="finding-severity">{f.severity}</span>
                    <span className="ds-chip-tag">{f.type}</span>
                    <strong className="ag-wrap">{clean(f.tool)}</strong>
                    <span className="pill ag-status">{fmtStatus(f.status)}</span>
                  </div>
                  {nViol > 1 && <div className="ag-finding-viol" data-testid="finding-violations">{nViol} violations in this run: {toolCounts(viol)}</div>}
                  <div className="ag-reason" data-testid="finding-reason"><Markdown text={clean(f.explanation) || 'No explanation recorded.'} /></div>
                  {src && (
                    <span className="ag-tag" data-testid="finding-explanation-source">
                      {f.detail && f.detail.explanation_model ? 'mock text (not a model)' : 'template text (no model)'}
                    </span>
                  )}
                </div>
                <div className="ag-finding-side">
                  <span className="ds-nowrap ag-time" data-testid="finding-time">{fmtTs(f.ts)}</span>
                  {f.run_id && (
                    <Link className="ds-btn ds-btn-ghost ag-sm ag-finding-link" data-testid="finding-timeline-link"
                          to={`/runs/${encodeURIComponent(f.run_id)}${spanId ? `?span=${encodeURIComponent(spanId)}` : ''}`}>
                      View run timeline<Arrow />
                    </Link>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

function Runs({ runs, onRetry }) {
  const navigate = useNavigate()
  const list = runs.data || []
  const go = (rid) => navigate(`/runs/${encodeURIComponent(rid)}`)
  return (
    <Card title="Runs" className="ag-ord-4" actions={runs.data ? <span className="ag-count">{nf(list.length)}</span> : null}>
      {runs.error && !runs.data && <InlineError error={runs.error} what="runs" onRetry={onRetry} />}
      {runs.loading && !runs.data && !runs.error && <Skel what="runs" />}
      {runs.data && !list.length && <Empty title="No runs recorded." hint="Runs appear here once the agent's traces have been ingested." />}
      {list.length > 0 && (
        <table className="ds-table ag-table ag-runs">
          <colgroup><col className="c-rrun" /><col className="c-rstart" /><col className="c-rspans" /><col className="c-rgo" /></colgroup>
          <thead><tr><th scope="col">Run</th><th scope="col">Started</th><th scope="col" className="num">Spans</th><th scope="col"><span className="ds-sr">Open</span></th></tr></thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.run_id} data-testid="run-item" onClick={() => go(r.run_id)}>
                <td><span className="ag-idv">
                  <Link className="ds-row-link ag-mono ds-trunc ag-idv-text" to={`/runs/${encodeURIComponent(r.run_id)}`} title={r.run_id} onClick={(e) => e.stopPropagation()}>{clean(r.run_id)}</Link>
                  <CopyButton value={r.run_id} label="run ID" />
                </span></td>
                <td className="ds-nowrap">{fmtTs(r.started_at)}</td>
                <td className="num ds-nowrap">{nf(r.span_count)} {Number(r.span_count) === 1 ? 'span' : 'spans'}</td>
                <td className="ag-go"><ChevronRight /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function Overview({ a }) {
  return (
    <Card title="Overview" className="ag-ord-1">
      <Dl testid="agent-details" className="ag-dl-tight" rows={[
        ['Agent ID', <IdValue key="i" value={a.id} label="agent ID" />],
        ['Source', sourceLabel(a.source_platform)],
        ['Owner', a.owner ? <IdValue key="o" value={clean(a.owner)} label="owner" testid="agent-owner" plain /> : <None>—</None>],
        ['Origin', clean(a.origin) || <None>—</None>],
        ['Last seen', <span className="ds-nowrap ag-ink">{fmtTs(a.last_seen)}</span>],
      ]} />
    </Card>
  )
}

export default function Agent() {
  const { id } = useParams()
  return <AgentView key={id} id={id} />
}

function PageSkel() {
  return (
    <div className="ds-page ds-container" role="status" aria-busy="true" data-testid="agent-loading">
      <span className="ds-sr">Loading agent ...</span>
      <span className="ds-skel" style={{ width: 200, height: 14, marginBottom: 16 }} />
      <span className="ds-skel" style={{ width: 360, height: 36, marginBottom: 12 }} />
      <span className="ds-skel" style={{ width: 280, height: 20, marginBottom: 24 }} />
      <div className="ag-cols">
        <div className="ag-main">{[157, 134, 122].map((h, i) => <div key={i} className="ds-card ag-card" style={{ minHeight: h }}><div className="ag-card-body"><Skel what="" lines={2} /></div></div>)}</div>
        <div className="ag-side">{[258, 354].map((h, i) => <div key={i} className="ds-card ag-card" style={{ minHeight: h }}><div className="ag-card-body"><Skel what="" lines={3} /></div></div>)}</div>
      </div>
    </div>
  )
}

function AgentView({ id }) {
  const [rev, setRev] = React.useState(0)
  const [runRev, setRunRev] = React.useState(0)
  const [fRev, setFRev] = React.useState(0)
  const agent = useAsync(() => api.agent(id), [id, rev])
  const gov = useAsync(() => api.governance(id), [id, rev])
  const fnd = useAsync(() => api.findings(id), [id, fRev])
  const runs = useAsync(() => fetch(`api/agents/${encodeURIComponent(id)}/runs`).then((r) => {
    if (!r.ok) throw new Error(`${r.status}: could not load runs`)
    return r.json()
  }), [id, runRev])
  const demo = useHealth()
  const { hash } = useLocation()
  const navigate = useNavigate()
  const [hl, setHl] = React.useState(null)
  const hlTimer = React.useRef(null)
  React.useEffect(() => () => clearTimeout(hlTimer.current), [])
  // Arriving from a run violation (#finding-<id>): scroll to that finding, flash it once, then clear the hash.
  React.useEffect(() => {
    if (!hash || !/^#finding-/.test(hash) || !fnd.data) return
    const el = document.getElementById(hash.slice(1))
    if (!el) return
    el.scrollIntoView({ block: 'center' })
    setHl(hash.slice(1))
    clearTimeout(hlTimer.current)
    hlTimer.current = setTimeout(() => setHl(null), 2600)
    navigate({ hash: '' }, { replace: true })
  }, [hash, fnd.data, navigate])
  const pack = useEvidencePack(id)
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const [ok, setOk] = React.useState(null)
  const [runBusy, setRunBusy] = React.useState(false)
  const [runErr, setRunErr] = React.useState(null)

  if (agent.error && !agent.data) {
    return <ErrorPage title="We could not load this agent" message={`${agent.error.status ? agent.error.status + ': ' : ''}${clean(agent.error.message)}`}
      onRetry={() => setRev((r) => r + 1)} back={<Link className="ds-btn ds-btn-ghost" to="/">Back to Fleet</Link>} />
  }
  if (!agent.data) return <PageSkel />
  const a = agent.data
  const isDemo = !!(demo && demo.demo_mode)

  const approve = async (asDemo) => {
    setBusy(true); setErr(null); setOk(null)
    try {
      const r = await (asDemo === true ? demoApi.approve(id) : api.approve(id))
      setOk(`Approved${asDemo === true ? ' by the simulated demo approver' : ''}. Expires ${fmtTs(r.expires_at)}.`)
      setRev((x) => x + 1)
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const runDemo = async () => {
    setRunBusy(true); setRunErr(null)
    try { await demoApi.run(id); setRunRev((x) => x + 1) } catch (e) { setRunErr(e.message) } finally { setRunBusy(false) }
  }

  return (
    <div className="ds-page ds-container" data-testid="agent-page">
      <Breadcrumb items={[{ label: 'Fleet', to: '/' }, { label: clean(a.name) }]} />
      <header className="ds-page-header ag-header" data-testid="agent-header">
        <div className="ds-page-header-row ag-header-row">
          <div className="ag-header-main">
            <h1 className="ds-title ag-name" title={clean(a.name)}>{clean(a.name)}</h1>
            <div className="ag-meta">
              {a.connection_id
                ? <Link className="ds-chip-tag ag-source-link" data-testid="agent-source" to={`/connections/${a.connection_id}`} title="Open the connection this agent came from">{sourceLabel(a.source_platform)}<span className="ds-sr"> connection</span></Link>
                : <span className="ds-chip-tag" data-testid="agent-source">{sourceLabel(a.source_platform)}</span>}
              {a.sample && <SampleBadge />}
              {a.source_disconnected && <span className="ds-chip-tag ag-gone" data-testid="agent-source-disconnected">Source disconnected</span>}
              <TierBadge tier={a.tier} />
              <StatusPill status={a.status} testid="agent-status" />
              <span className="ag-muted ag-meta-text">Owner {clean(a.owner) || '—'} · Last seen {fmtTs(a.last_seen)}</span>
            </div>
          </div>
          <div className="ag-actions" data-testid="agent-actions">
            <button type="button" className="ds-btn ds-btn-primary ag-sm" onClick={() => approve(false)} disabled={busy}>Approve</button>
            <button type="button" className="ds-btn ag-sm" data-testid="evidence-pack-generate" onClick={pack.generate} disabled={pack.busy} aria-busy={pack.busy || undefined}>
              {pack.busy ? 'Generating...' : 'Generate evidence pack'}</button>
            {isDemo && <button type="button" className="ds-btn ag-sm" onClick={() => approve(true)} disabled={busy}>Approve as demo approver</button>}
            {isDemo && a.name === 'recon-agent' && <button type="button" className="ds-btn ag-sm" onClick={runDemo} disabled={runBusy}>Run recon agent</button>}
          </div>
        </div>
      </header>
      {(err || ok || runErr) && (
        <div className="ag-alerts">
          {err && <div className="ds-alert ds-alert-error" role="alert" data-testid="approve-error">{approveError(err)}</div>}
          {runErr && <div className="ds-alert ds-alert-error" role="alert">{runErr}</div>}
          {ok && <div className="ds-alert ag-ok" role="status" data-testid="approve-success">{ok}</div>}
        </div>
      )}
      {agent.error && <div className="ag-alerts"><InlineError error={agent.error} what="the latest agent data" onRetry={() => setRev((r) => r + 1)} /></div>}
      {a.origin === 'external' && <div className="ag-passport"><Passport agent={a} /></div>}
      <div className="ag-cols">
        <div className="ag-main">
          <Findings fnd={fnd} hl={hl} onRetry={() => setFRev((r) => r + 1)} />
          <Runs runs={runs} onRetry={() => setRunRev((r) => r + 1)} />
          <Approvals gov={gov.data} error={gov.error} onRetry={() => setRev((r) => r + 1)} />
          <Validation id={id} />
          <Governance gov={gov.data} error={gov.error} onRetry={() => setRev((r) => r + 1)} />
          <Dependencies id={id} />
        </div>
        <div className="ag-side">
          <Overview a={a} />
          <TierCard agent={a} />
          <Identity agent={a} />
          <Manifest agent={a} />
        </div>
      </div>
      <div id="evidence-pack-slot" data-slot="evidence-pack" className="ag-evidence-row"><EvidencePack pack={pack} /></div>
    </div>
  )
}
