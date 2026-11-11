import React from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { agentPath, clean, fmtTs, relTime, sourceLabel } from '../format'
import { StatusPill, TierBadge, useAsync } from '../components/ui'
import { Button, PageHeader } from '../components/form'

const COLS = [['Source', 'c-source'], ['Name', 'c-name'], ['Owner', 'c-owner'], ['Tier', 'c-tier'], ['Status', 'c-status'], ['Open findings', 'c-findings'], ['Last seen', 'c-seen']]

function ColGroup() {
  return <colgroup>{COLS.map(([c, k]) => <col key={c} className={k} />)}</colgroup>
}

function Tile({ id, number, label, hint, active, onClick }) {
  return (
    <button type="button" className={`ds-tile${active ? ' is-active' : ''}`} data-testid={`fleet-tile-${id}`}
            aria-pressed={active} aria-label={`${label}: ${number}`} onClick={onClick}>
      <span className="ds-tile-num">{number}</span>
      <span className="ds-tile-label">{label}</span>
      <span className="ds-tile-hint">{hint}</span>
    </button>
  )
}

const SKEL_W = ['70%', '75%', '65%', '60%', '70%', '40%', '70%']
function Skeleton() {
  return (
    <div className="ds-card ds-table-card" data-testid="fleet-skeleton" role="status" aria-busy="true">
      <span className="ds-sr">Loading the agent list</span>
      <table className="ds-table" aria-hidden="true">
        <ColGroup />
        <thead><tr>{COLS.map(([c]) => <th key={c} scope="col"><span className="ds-skel ds-skel-head" /></th>)}</tr></thead>
        <tbody>
          {Array.from({ length: 6 }, (_, i) => (
            <tr key={i}>{SKEL_W.map((w, j) => <td key={j}><span className="ds-skel" style={{ width: w }} /></td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const has = (v, t) => (clean(v) || '').toLowerCase().includes(t)

// Plain clicks and Enter open the agent; modified clicks, middle clicks and the end of a text selection do not.
function rowClick(e, go) {
  if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return
  if (e.target.closest('a')) return
  const sel = window.getSelection && window.getSelection()
  if (sel && !sel.isCollapsed && sel.toString().trim()) return
  go()
}

export default function Fleet() {
  const nav = useNavigate()
  const [rev, setRev] = React.useState(0)
  const [params, setParams] = useSearchParams()
  const source = params.get('source') || ''
  const tier = params.get('tier') || ''
  const q = params.get('q') || ''
  const quick = ['findings', 'pending'].includes(params.get('tile')) ? params.get('tile') : ''
  const setP = (patch) => setParams((cur) => {
    const next = new URLSearchParams(cur)
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    return next
  }, { replace: true })
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
  const rows = needle ? byFacets.filter((a) => has(a.name, needle) || has(a.owner, needle)) : byFacets
  const filtered = !!(source || tier || needle || quick)
  const clear = () => setP({ source: '', tier: '', q: '', tile: '' })
  const toggleTier = () => setP({ tier: tier === '1' ? '' : '1' })
  const toggleQuick = (k) => setP({ tile: quick === k ? '' : k })
  const ready = !!data
  const n = (v) => (ready ? v : '–')

  return (
    <div className="ds-page ds-container" data-testid="fleet-page">
      <PageHeader title="Fleet" description="Every AI agent across your estate, with its tier, owner and open findings."
                  descriptionTestId="fleet-description"
                  actions={<Button variant="primary" data-testid="register-external-button" onClick={() => nav('/register')}>Register external agent</Button>} />

      <div className="ds-tiles" role="group" aria-label="Fleet summary. Tiles are filters and combine with Source, Tier and search.">
        <Tile id="total" number={n(stats.total)} label="Total agents" hint="Whole fleet" active={false} onClick={clear} />
        <Tile id="tier1" number={n(stats.tier1)} label="Tier 1 agents" hint="Highest consequence" active={tier === '1'} onClick={toggleTier} />
        <Tile id="open-findings" number={n(stats.findings)} label="Open findings" hint={ready ? `Across ${stats.withFindings} ${stats.withFindings === 1 ? 'agent' : 'agents'}` : 'Across the fleet'} active={quick === 'findings'} onClick={() => toggleQuick('findings')} />
        <Tile id="pending-review" number={n(stats.pending)} label="Pending review" hint="Awaiting approval" active={quick === 'pending'} onClick={() => toggleQuick('pending')} />
      </div>

      <div className="ds-filterbar" role="search">
        <div className="ds-filter ds-search-filter">
          <label htmlFor="fleet-search">Search agents</label>
          <div className="ds-search">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          <input id="fleet-search" type="search" className="ds-input" aria-label="Search agents" placeholder="Search by name or owner"
                 value={q} onChange={(e) => setP({ q: e.target.value })} />
          </div>
        </div>
        <label className="ds-filter">Source
          <select className="ds-input ds-select" data-testid="source-filter" value={source} onChange={(e) => setP({ source: e.target.value })}>
            <option value="">All</option>
            {sources.map((s) => <option key={s} value={s}>{sourceLabel(s)}</option>)}
          </select>
        </label>
        <label className="ds-filter">Tier
          <select className="ds-input ds-select" data-testid="tier-filter" value={tier} onChange={(e) => setP({ tier: e.target.value })}>
            <option value="">All</option>
            {[1, 2, 3, 4].map((t) => <option key={t} value={t}>Tier {t}</option>)}
          </select>
        </label>
        {filtered && !(ready && rows.length === 0) && <Button variant="ghost" onClick={clear}>Clear filters</Button>}
        <span className="ds-count" data-testid="fleet-count" aria-live="polite">{ready ? `${rows.length} ${rows.length === 1 ? 'agent' : 'agents'}` : ''}</span>
      </div>

      {all.error && !data && (
        <div className="ds-card ds-empty ds-empty-error" data-testid="fleet-error" role="alert">
          <svg className="ds-empty-icon" width="32" height="32" viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M10 5.5v5.5M10 13.5v1.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <h2 className="ds-empty-title">We could not load the fleet</h2>
          <p className="ds-helper">Something went wrong while fetching agents. Check your connection and try again.</p>
          <Button variant="secondary" onClick={() => setRev((r) => r + 1)}>Try again</Button>
        </div>
      )}
      {all.loading && !data && !all.error && <Skeleton />}
      {ready && rows.length === 0 && !filtered && (
        <div className="ds-card ds-empty" data-testid="fleet-empty">
          <h2 className="ds-empty-title">No agents yet</h2>
          <p className="ds-helper">Register an external agent to start tracking its tier, owner and findings.</p>
          <Button variant="primary" onClick={() => nav('/register')}>Register external agent</Button>
        </div>
      )}
      {ready && rows.length === 0 && filtered && (
        <div className="ds-card ds-empty" data-testid="fleet-empty">
          <h2 className="ds-empty-title">No agents match</h2>
          <p className="ds-helper">Try a different name, source or tier.</p>
          <Button variant="ghost" onClick={clear}>Clear filters</Button>
        </div>
      )}
      {ready && rows.length > 0 && (
        <div className="ds-card ds-table-card">
          <table className="ds-table">
            <ColGroup />
            <thead><tr>{COLS.map(([c]) => <th key={c} scope="col" className={c === 'Open findings' ? 'num' : undefined}>{c}</th>)}</tr></thead>
            <tbody>
              {rows.map((a) => {
                const go = () => nav(agentPath(a.id))
                const name = clean(a.name)
                const owner = clean(a.owner) || '-'
                return (
                  <tr key={a.id} data-testid="fleet-row" data-agent-id={a.id} tabIndex={0}
                      onClick={(e) => rowClick(e, go)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) go() }}>
                    <td data-testid="fleet-source" data-source={a.source_platform}><span className="ds-chip-tag">{sourceLabel(a.source_platform)}</span></td>
                    <td data-testid="fleet-name" className="ds-trunc" title={name}><Link to={agentPath(a.id)} className="ds-row-link">{name}</Link>{a.card_host && <span className="host" data-testid="fleet-card-host">{clean(a.card_host)}</span>}</td>
                    <td data-testid="fleet-owner" className="ds-trunc" title={owner}><span className="ds-trunc-text" title={owner}>{owner}</span></td>
                    <td><TierBadge tier={a.tier} /></td>
                    <td data-testid="fleet-status"><StatusPill status={a.status} /></td>
                    <td className="num"><span data-testid="open-findings" className={a.open_findings > 0 ? 'count hot' : 'count'}>{a.open_findings}</span></td>
                    <td data-testid="fleet-last-seen" className="ds-nowrap"><time dateTime={a.last_seen || undefined} title={fmtTs(a.last_seen)}>{relTime(a.last_seen)}</time></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
