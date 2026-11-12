// Step 6: Import summary. What was imported, where to find it, and the sync schedule (Daily at 02:00 by default).
import React from 'react'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { agentsApi, scansApi } from '../api'
import WizardFrame from '../components/connect/WizardFrame'
import ScheduleCard, { useSchedule } from '../components/connect/ScheduleCard'
import { ErrorPanel, IconCheck, SampleBadge, useConn } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

// The import result survives a reload: router state, then session storage, then the staged rows themselves.
function fromRows(rows) {
  const done = rows.filter((r) => r.imported_agent_id && r.class !== 'missing')
  return {
    imported: done.filter((r) => r.class === 'new').length, updated: done.filter((r) => r.class === 'changed').length,
    no_owner: done.filter((r) => r.class === 'new' && !r.owner).length, missing: rows.filter((r) => r.class === 'missing').length,
    agent_ids: done.map((r) => r.imported_agent_id),
  }
}

export default function ImportSummary() {
  const { connId, scanId } = useParams()
  const loc = useLocation()
  const c = useConn(connId)
  const sched = useSchedule(connId)
  const [result, setResult] = React.useState(() => {
    if (loc.state?.result) return loc.state.result
    try { return JSON.parse(sessionStorage.getItem(`act-import-${scanId}`)) } catch { return null }
  })
  const [missing, setMissing] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [agents, setAgents] = React.useState([])
  const [rev, setRev] = React.useState(0)

  React.useEffect(() => {
    let live = true
    setError(false)
    scansApi.staged(scanId).then((r) => {
      if (!live) return
      const rows = r.rows || []
      setMissing(rows.filter((x) => x.class === 'missing').length)
      setResult((cur) => cur || fromRows(rows))
    }, () => live && setError(true))
    agentsApi.list().then((a) => live && setAgents(a || []), () => {})
    return () => { live = false }
  }, [scanId, rev])

  const p = PROVIDERS[c.conn?.provider]
  if (c.error || error) return <div className="ds-page ds-container po-page"><ErrorPanel title="ACT couldn't load the import result" onRetry={() => { c.reload(); setRev((r) => r + 1) }}>The import is saved. Try again in a moment.</ErrorPanel></div>
  if (c.conn && !p) return <Navigate to="/connections" replace />
  if (!c.conn || !result) return <div className="ds-page ds-container po-page" aria-busy="true"><span className="ds-skel po-skel-title" /><span className="ds-skel po-skel-row" /></div>

  const sample = !!c.conn.sample
  const total = (result.imported || 0) + (result.updated || 0)
  const ids = new Set(result.agent_ids || [])
  const tier1 = agents.filter((a) => ids.has(a.id) && a.tier === 1).length
  const missingN = missing ?? result.missing ?? 0
  const fleet = `/?connection=${encodeURIComponent(connId)}&tile=pending`

  return (
    <WizardFrame provider={c.conn.provider} connId={connId} step={6} title="Import complete" hideAside
                 crumbs={[{ label: 'Connections', to: '/connections' }, { label: p.name, to: `/connections/${connId}` }, { label: 'Import' }]}
                 hrefs={{ 3: `/connect/${c.conn.provider}/${connId}/scope`, 5: `/connections/${connId}/scans/${scanId}/review` }}
                 description="Every imported agent is in Pending review with an audit event naming you, this connection and this scan.">
      <section className="ds-card" data-testid="import-summary" aria-labelledby="sum-h">
        <div className="po-sum-hero">
          <span className="po-sum-ico" aria-hidden="true"><IconCheck /></span>
          <div>
            <h2 id="sum-h">{plural(total, 'agent', 'agents')} imported from {p.name}{sample && <> <SampleBadge /></>}</h2>
            <p className="ds-helper po-flat">{tier1 ? `${tier1} ${tier1 === 1 ? 'is' : 'are'} Tier 1. Reviewers see ${tier1 === 1 ? 'it' : 'them'} first in Fleet.` : 'They are waiting for a reviewer in Fleet.'}{sample && ' Sample agents are marked Sample data and are excluded from evidence packs.'}</p>
          </div>
        </div>
        <dl className="po-kv">
          <div><dt>New</dt><dd data-testid="import-summary-count-new">{result.imported || 0}<small>Created in Pending review</small></dd></div>
          <div><dt>Updated</dt><dd data-testid="import-summary-count-updated">{result.updated || 0}<small>Changed agents re-tiered; back in review if risk went up</small></dd></div>
          <div><dt>Need an owner</dt><dd data-testid="import-summary-count-no-owner">{result.no_owner || 0}<small>{result.no_owner ? "Assign one on the agent's page before approval" : 'Every agent has an owner'}</small></dd></div>
          <div><dt>Missing at source</dt><dd data-testid="import-summary-count-missing">{missingN}<small>{missingN ? 'Not found in this scan; the record stays' : 'Nothing disappeared'}</small></dd></div>
        </dl>
      </section>

      <div className="po-two">
        <section className="ds-card" aria-labelledby="nx-h">
          <div className="po-panel-head"><h2 id="nx-h">Next</h2></div>
          <div className="po-panel-body po-next">
            <Link className="ds-btn ds-btn-primary" to={fleet} data-testid="summary-open-fleet">Open imported agents in Fleet</Link>
            <Link className="ds-btn ds-btn-ghost" to={`/connections/${connId}`} data-testid="summary-open-connection">View the {p.name} connection</Link>
          </div>
        </section>
        <ScheduleCard suggestDaily connId={connId} provider={p.name} sample={sample} state={sched} />
      </div>
    </WizardFrame>
  )
}
