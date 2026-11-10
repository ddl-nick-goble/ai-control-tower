import React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { clean, fmtTs } from '../format'
import { StatusPill, TierBadge, useAsync } from '../components/ui'
import { Alert, Button, PageHeader } from '../components/form'

const COLS = ['Source', 'Name', 'Owner', 'Tier', 'Status', 'Open findings', 'Last seen']

function Tile({ id, number, label, hint, active, onClick }) {
  return (
    <button type="button" className={`ds-tile${active ? ' is-active' : ''}`} data-testid={`fleet-tile-${id}`}
            aria-pressed={active} onClick={onClick}>
      <span className="ds-tile-num">{number}</span>
      <span className="ds-tile-label">{label}</span>
      <span className="ds-tile-hint">{hint}</span>
    </button>
  )
}

function Skeleton() {
  return (
    <div className="ds-card ds-table-card" data-testid="fleet-skeleton" aria-busy="true" aria-label="Loading agents">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="ds-skel-row"><span className="ds-skel" style={{ width: 64 }} /><span className="ds-skel" style={{ width: '28%' }} />
          <span className="ds-skel" style={{ width: '14%' }} /><span className="ds-skel" style={{ width: 56 }} /><span className="ds-skel" style={{ width: 88 }} /></div>
      ))}
    </div>
  )
}

export default function Fleet() {
  const nav = useNavigate()
  const [rev, setRev] = React.useState(0)
  const [source, setSource] = React.useState('')
  const [tier, setTier] = React.useState('')
  const [q, setQ] = React.useState('')
  const [quick, setQuick] = React.useState('') // '' | 'findings' | 'pending'
  const all = useAsync(() => api.agents(), [rev])
  const data = all.data
  const sources = React.useMemo(() => [...new Set((data || []).map((a) => a.source_platform))].sort(), [data])
  const stats = React.useMemo(() => {
    const l = data || []
    return {
      total: l.length, tier1: l.filter((a) => a.tier === 1).length,
      findings: l.reduce((n, a) => n + (a.open_findings || 0), 0),
      withFindings: l.filter((a) => a.open_findings > 0).length,
      pending: l.filter((a) => a.status === 'pending_review').length,
    }
  }, [data])

  const needle = q.trim().toLowerCase()
  const byFacets = (data || []).filter((a) =>
    (!source || a.source_platform === source) && (!tier || String(a.tier) === tier) &&
    (!quick || (quick === 'findings' ? a.open_findings > 0 : a.status === 'pending_review')))
  // Names win: a query that matches agent names narrows by name; otherwise it is matched against owners.
  const has = (v, t) => (clean(v) || '').toLowerCase().includes(t)
  const nameHits = needle ? byFacets.filter((a) => has(a.name, needle)) : byFacets
  const rows = !needle || nameHits.length ? nameHits : byFacets.filter((a) => has(a.owner, needle))
  const filtered = !!(source || tier || needle || quick)
  const clear = () => { setSource(''); setTier(''); setQ(''); setQuick('') }
  const toggleTier = () => { setQuick(''); setTier((t) => (t === '1' ? '' : '1')) }
  const toggleQuick = (k) => { setTier(''); setQuick((c) => (c === k ? '' : k)) }
  const open = (a) => nav(`/agents/${encodeURIComponent(a.id).replace(/%3A/gi, ":")}`)
  const ready = !!data
  const n = (v) => (ready ? v : '–')

  return (
    <div className="ds-page ds-container" data-testid="fleet-page">
      <PageHeader title="Fleet" description="Every AI agent across your estate, with its tier, owner and open findings."
                  descriptionTestId="fleet-description"
                  actions={<Button variant="primary" data-testid="register-external-button" onClick={() => nav('/register')}>Register external agent</Button>} />

      <div className="ds-tiles" role="group" aria-label="Fleet summary, select to filter">
        <Tile id="total" number={n(stats.total)} label="Total agents" hint="Whole fleet" active={ready && !filtered} onClick={clear} />
        <Tile id="tier1" number={n(stats.tier1)} label="Tier 1 agents" hint="Highest consequence" active={tier === '1' && !quick} onClick={toggleTier} />
        <Tile id="open-findings" number={n(stats.findings)} label="Open findings" hint={ready ? `Across ${stats.withFindings} ${stats.withFindings === 1 ? 'agent' : 'agents'}` : ' '} active={quick === 'findings'} onClick={() => toggleQuick('findings')} />
        <Tile id="pending-review" number={n(stats.pending)} label="Pending review" hint="Awaiting approval" active={quick === 'pending'} onClick={() => toggleQuick('pending')} />
      </div>

      <div className="ds-filterbar" role="search">
        <div className="ds-search">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          <input type="search" className="ds-input" aria-label="Search agents" placeholder="Search by name or owner"
                 value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label className="ds-filter">Source
          <select className="ds-input ds-select" data-testid="source-filter" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All</option>
            {sources.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="ds-filter">Tier
          <select className="ds-input ds-select" data-testid="tier-filter" value={tier} onChange={(e) => { setQuick(''); setTier(e.target.value) }}>
            <option value="">All</option>
            {[1, 2, 3, 4].map((t) => <option key={t} value={t}>Tier {t}</option>)}
          </select>
        </label>
        {filtered && <Button variant="ghost" onClick={clear}>Clear filters</Button>}
        <span className="ds-count" data-testid="fleet-count" aria-live="polite">{ready ? `${rows.length} ${rows.length === 1 ? 'agent' : 'agents'}` : ''}</span>
      </div>

      {all.error && !data && (
        <div className="ds-state" data-testid="fleet-error" role="alert">
          <Alert title="We could not load the fleet">Something went wrong while fetching agents. Check your connection and try again.</Alert>
          <Button variant="secondary" onClick={() => setRev((r) => r + 1)}>Try again</Button>
        </div>
      )}
      {all.loading && !data && !all.error && <Skeleton />}
      {ready && rows.length === 0 && (
        <div className="ds-card ds-empty" data-testid="fleet-empty">
          <h2 className="ds-empty-title">No agents match</h2>
          <p className="ds-helper">No agents match the current filters. Clear them to see the whole fleet.</p>
          <Button variant="secondary" onClick={clear}>Clear filters</Button>
        </div>
      )}
      {ready && rows.length > 0 && (
        <div className="ds-card ds-table-card">
          <table className="ds-table">
            <thead><tr>{COLS.map((c) => <th key={c} scope="col" className={c === 'Open findings' ? 'num' : undefined}>{c}</th>)}</tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} data-testid="fleet-row" data-agent-id={a.id} tabIndex={0}
                    onClick={(e) => { if (!e.target.closest('a')) open(a) }}
                    onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) open(a) }}>
                  <td data-testid="fleet-source"><span className="ds-chip-tag">{a.source_platform}</span></td>
                  <td data-testid="fleet-name"><Link to={`/agents/${encodeURIComponent(a.id)}`} className="ds-row-link">{clean(a.name)}</Link>{a.card_host && <span className="host" data-testid="fleet-card-host">{clean(a.card_host)}</span>}</td>
                  <td data-testid="fleet-owner">{clean(a.owner) || '-'}</td>
                  <td><TierBadge tier={a.tier} /></td>
                  <td data-testid="fleet-status"><StatusPill status={a.status} /></td>
                  <td className="num"><span data-testid="open-findings" className={a.open_findings > 0 ? 'count hot' : 'count'}>{a.open_findings}</span></td>
                  <td data-testid="fleet-last-seen" className="ds-nowrap">{fmtTs(a.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
