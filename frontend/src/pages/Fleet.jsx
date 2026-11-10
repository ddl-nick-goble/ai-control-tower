import React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { clean, fmtTs } from '../format'
import { ErrorBox, Loading, StatusPill, TierBadge, useAsync } from '../components/ui'

export default function Fleet() {
  const nav = useNavigate()
  const [source, setSource] = React.useState('')
  const [tier, setTier] = React.useState('')
  const all = useAsync(() => api.agents(), [])
  const list = useAsync(() => api.agents({ source, tier }), [source, tier])
  const sources = React.useMemo(
    () => [...new Set((all.data || []).map((a) => a.source_platform))].sort(), [all.data])

  return (
    <div>
      <div className="page-head">
        <h1>Fleet</h1>
        <button className="btn primary" data-testid="register-external-button" onClick={() => nav('/register')}>
          Register external agent
        </button>
      </div>
      <div className="filters">
        <label>Source{' '}
          <select className="select" data-testid="source-filter" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All</option>
            {sources.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>Tier{' '}
          <select className="select" data-testid="tier-filter" value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="">All</option>
            {[1, 2, 3, 4].map((t) => <option key={t} value={t}>Tier {t}</option>)}
          </select>
        </label>
      </div>
      {list.error && <ErrorBox error={list.error} />}
      {list.loading && !list.data && <Loading what="agents" />}
      {list.data && (
        <table className="table">
          <thead>
            <tr><th>Source</th><th>Name</th><th>Owner</th><th>Tier</th><th>Status</th><th>Open findings</th><th>Last seen</th></tr>
          </thead>
          <tbody>
            {list.data.map((a) => (
              <tr key={a.id} data-testid="fleet-row" data-agent-id={a.id}>
                <td data-testid="fleet-source"><span className="chip">{a.source_platform}</span></td>
                <td data-testid="fleet-name"><Link to={`/agents/${encodeURIComponent(a.id)}`}>{clean(a.name)}</Link>{a.card_host && <span className="host" data-testid="fleet-card-host">{clean(a.card_host)}</span>}</td>
                <td data-testid="fleet-owner">{clean(a.owner) || '-'}</td>
                <td><TierBadge tier={a.tier} /></td>
                <td data-testid="fleet-status"><StatusPill status={a.status} /></td>
                <td><span data-testid="open-findings" className={a.open_findings ? 'count hot' : 'count'}>{a.open_findings}</span></td>
                <td data-testid="fleet-last-seen">{fmtTs(a.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
