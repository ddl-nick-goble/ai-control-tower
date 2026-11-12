// Step 5: Review. The staged rows of one scan in a table: New, Changed, Unchanged and Missing, a suggested tier with its
// "why", owners, risk flags, and one Import button that states the count.
import React from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { agentsApi, scansApi } from '../api'
import { Alert } from '../components/form'
import WizardFrame from '../components/connect/WizardFrame'
import { ErrorPanel, SampleBadge, SampleBanner, useConn } from '../components/connect/bits'
import {
  CriteriaList, DiffList, OwnerSelect, TierBlock, TierCell, isRisky, isSelectable, rowFlags, tierOf,
} from '../components/connect/intake'
import { PROVIDERS } from '../components/connect/providers'
import { sourceLabel } from '../format'

const FILTERS = [['all', 'All'], ['new', 'New'], ['changed', 'Changed'], ['attention', 'Needs attention'], ['unchanged', 'Unchanged'], ['missing', 'Missing']]
const CHG = { new: 'New', changed: 'Changed', unchanged: 'Unchanged', missing: 'Missing' }
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

const matches = (row, f) => (f === 'all' ? true : f === 'attention' ? isSelectable(row) && rowFlags(row).length > 0 : row.class === f)

function Flags({ row, provider }) {
  if (row.class === 'missing') return <span className="po-flag-note">Not found in {provider}. Sync raises a finding; the record stays.</span>
  const flags = rowFlags(row)
  if (!flags.length) return <span className="ds-helper po-flat">None</span>
  const shown = flags.slice(0, 2), rest = flags.slice(2)
  return (
    <div className="po-flags">
      {shown.map((f) => <span key={f.key} className={`po-flag${f.high ? ' po-flag-high' : ''}`}>{f.label}</span>)}
      {rest.length > 0 && <span className="po-flag" title={rest.map((f) => f.label).join(', ')}>+{rest.length}<span className="ds-sr"> more: {rest.map((f) => f.label).join(', ')}</span></span>}
    </div>
  )
}

function Row({ row, provider, owners, open, onToggle, selected, onSelect, onOwner, ownerSet, onUpdated }) {
  const selectable = isSelectable(row)
  const t = tierOf(row)
  const sub = `${sourceLabel(row.platform)} · ${row.native_id}`
  const diffN = row.diff ? (Array.isArray(row.diff) ? row.diff.length : Object.keys(row.diff).length) : 0
  return (
    <>
      <tr data-testid={`review-row-${row.id}`} className={`${isRisky(row) && row.class !== 'missing' ? 'is-risk' : ''}${selectable ? '' : ' is-off'}`}>
        <td>{selectable && <input type="checkbox" id={`sel-${row.id}`} checked={selected} data-testid={`review-select-${row.id}`} aria-label={`Import ${row.name}`} onChange={(e) => onSelect(e.target.checked)} />}</td>
        <td>
          <span className="po-aname-row"><span className="po-aname" title={row.name}>{row.name}</span>{row.sample && <SampleBadge />}</span>
          <span className="po-asub" title={sub}>{sub}</span>
        </td>
        <td data-testid={`review-change-${row.id}`}>
          <span className={`po-chg po-chg-${row.class}`}>{CHG[row.class] || row.class}</span>
          {row.class === 'changed' && <span className="po-asub">{plural(diffN, 'field', 'fields')} changed</span>}
        </td>
        <td data-testid={`review-owner-${row.id}`}>
          {selectable
            ? <OwnerSelect row={row} owners={owners} label={`Owner for ${row.name}`} setByYou={ownerSet} onChange={onOwner} />
            : <span className="po-own-ro">{row.owner || 'No owner'}</span>}
        </td>
        <td data-testid={`review-tier-${row.id}`}><TierCell row={row} /></td>
        <td data-testid={`review-flag-${row.id}`}><Flags row={row} provider={provider} /></td>
        <td>
          {row.class !== 'missing' && (
            <button type="button" className="ds-btn ds-btn-ghost po-why-btn" id={`why-${row.id}`} data-testid={`review-why-${row.id}`}
                    aria-expanded={open ? 'true' : 'false'} aria-controls={`xp-${row.id}`} onClick={onToggle}>
              Why<span className="ds-sr"> {row.name} got Tier {t}</span>
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="po-detail" id={`xp-${row.id}`} data-testid={`intake-panel-${row.id}`}>
          <td colSpan={7}>
            <div className="po-xp">
              <div>
                <h4>Why Tier {row.suggestion?.suggested_tier ?? t} is suggested</h4>
                <CriteriaList row={row} />
                <p className="ds-helper">Scores run from 1 (low) to 5 (high), from the agent's tools, policies and tags. The weighted score sets the tier.</p>
              </div>
              <div>
                {row.class === 'changed' && row.diff && <><DiffList row={row} testid={`review-diff-${row.id}`} /><div className="po-gap" /></>}
                <TierBlock row={row} canEdit={selectable} onUpdated={onUpdated} />
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function ScanReview() {
  const { connId, scanId } = useParams()
  const nav = useNavigate()
  const c = useConn(connId)
  const [rows, setRows] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [rev, setRev] = React.useState(0)
  const [owners, setOwners] = React.useState([])
  const [filter, setFilter] = React.useState('all')
  const [q, setQ] = React.useState('')
  const [sel, setSel] = React.useState(() => new Set())
  const [open, setOpen] = React.useState(null)
  const [ownerSet, setOwnerSet] = React.useState(() => new Set())
  const [importing, setImporting] = React.useState(false)
  const [importErr, setImportErr] = React.useState('')
  const [ownerErr, setOwnerErr] = React.useState('')
  const [announce, setAnnounce] = React.useState('')

  React.useEffect(() => {
    let live = true
    setError(false); setRows(null)
    scansApi.staged(scanId).then((r) => {
      if (!live) return
      const list = r.rows || []
      setRows(list)
      setSel(new Set(list.filter(isSelectable).map((x) => x.id)))
    }, () => live && setError(true))
    return () => { live = false }
  }, [scanId, rev])
  React.useEffect(() => {
    let live = true
    agentsApi.list().then((a) => live && setOwners([...new Set((a || []).map((x) => x.owner).filter((o) => o && o !== 'unassigned'))]), () => {})
    return () => { live = false }
  }, [])

  const provider = c.conn?.provider
  const p = PROVIDERS[provider]
  const sample = !!(c.conn?.sample || (rows || []).some((r) => r.sample))
  const counts = React.useMemo(() => {
    const out = {}
    FILTERS.forEach(([k]) => { out[k] = (rows || []).filter((r) => matches(r, k)).length })
    return out
  }, [rows])

  const needle = q.trim().toLowerCase()
  const visible = (rows || []).filter((r) => matches(r, filter) && (!needle || [r.name, r.native_id, r.platform, r.summary?.description].some((v) => (v || '').toLowerCase().includes(needle))))
  const selectableVisible = visible.filter(isSelectable)
  const allOn = selectableVisible.length > 0 && selectableVisible.every((r) => sel.has(r.id))
  const chosen = (rows || []).filter((r) => sel.has(r.id))
  const noOwner = chosen.filter((r) => !r.owner && r.class === 'new').length
  const missing = counts.missing || 0

  const patch = (id, fn) => setRows((rs) => rs.map((r) => (r.id === id ? fn(r) : r)))
  const setOwner = async (row, owner) => {
    patch(row.id, (r) => ({ ...r, owner: owner || null }))
    setOwnerSet((s) => new Set(s).add(row.id)); setOwnerErr('')
    try { await scansApi.patch(row.id, { owner }) } catch { setOwnerErr(`ACT couldn't save the owner for ${row.name}.`); patch(row.id, (r) => ({ ...r, owner: row.owner })) }
  }
  const bulkOwner = async (owner) => {
    if (!owner) return
    await Promise.all(chosen.map((r) => setOwner(r, owner)))
    setAnnounce(`Owner ${owner} set on ${plural(chosen.length, 'agent', 'agents')}.`)
  }
  const updated = (r) => patch(r.id, (old) => ({ ...old, ...r }))

  const runImport = async () => {
    setImporting(true); setImportErr('')
    try {
      const out = await scansApi.importRows(scanId, chosen.map((r) => r.id))
      try { sessionStorage.setItem(`act-import-${scanId}`, JSON.stringify(out)) } catch { /* private mode: the summary rebuilds from the rows */ }
      nav(`/connections/${connId}/scans/${scanId}/summary`, { state: { result: out } })
    } catch (e) {
      setImportErr(e.message || 'The database did not answer.')
      setImporting(false)
    }
  }

  if (c.error) return <div className="ds-page ds-container po-page"><ErrorPanel title="ACT couldn't load this connection" onRetry={c.reload}>Try again in a moment.</ErrorPanel></div>
  if (c.conn && !p) return <Navigate to="/connections" replace />
  if (!c.conn) return <div className="ds-page ds-container po-page" aria-busy="true"><span className="ds-skel po-skel-title" /><span className="ds-skel po-skel-row" /><span className="ds-skel po-skel-row" /></div>

  const found = (rows || []).filter((r) => r.class !== 'missing').length
  const nothingNew = rows && rows.length > 0 && !rows.some(isSelectable)
  const label = chosen.length ? `Import ${plural(chosen.length, 'agent', 'agents')}${sample ? ' as sample data' : ''}` : 'Import'

  return (
    <WizardFrame provider={provider} connId={connId} step={5} title="Review agents" hideAside
                 crumbs={[{ label: 'Connections', to: '/connections' }, { label: p.name, to: `/connections/${connId}` }, { label: 'Review' }]}
                 hrefs={{ 3: `/connect/${provider}/${connId}/scope`, 4: `/connections/${connId}/scans/${scanId}` }}
                 description={rows ? `Scan finished: ${plural(found, 'agent', 'agents')} found. New and changed agents are selected. Check owners and tiers, then import. Imported agents start in Pending review.` : 'Loading what the scan found.'}>
      {sample && <SampleBanner provider={p.name} review />}
      {error && (
        <ErrorPanel title="ACT couldn't load the scan results" onRetry={() => setRev((r) => r + 1)}>
          The scan is saved. This is a problem loading it; try again in a moment.
        </ErrorPanel>
      )}
      {!error && !rows && (
        <section className="ds-card po-panel" aria-busy="true" data-testid="review-loading">
          <div className="po-panel-body">{[0, 1, 2, 3].map((i) => <span key={i} className="ds-skel po-skel-row" />)}</div>
        </section>
      )}
      {rows && !error && (
        <>
          {importErr && (
            <div className="ds-alert ds-alert-error" role="alert" data-testid="review-import-error">
              <svg className="ds-alert-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M10 5.5v5.5M10 13.5v1.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
              <div><strong>Import didn't finish.</strong><p>{importErr}. Your selection is kept. Importing is safe to repeat: agents already imported are skipped.</p></div>
            </div>
          )}
          {ownerErr && <Alert title="An owner didn't save">{ownerErr} Try choosing it again.</Alert>}
          {nothingNew && (
            <div className="ds-alert ds-alert-info" role="status">
              <svg className="ds-alert-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M10 9v5M10 5.6v.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
              <div><strong>Nothing new to import.</strong><p>All {found} agents match what's in ACT. <Link to={`/connections/${connId}`}>Back to the connection</Link></p></div>
            </div>
          )}

          <div className="po-toolbar">
            <div className="po-seg" role="group" aria-label="Filter by change">
              {FILTERS.map(([k, name]) => (
                <button type="button" key={k} data-testid={`review-filter-${k}`} aria-pressed={filter === k ? 'true' : 'false'} onClick={() => setFilter(k)}>{name}<b>{counts[k]}</b></button>
              ))}
            </div>
            <div>
              <label className="ds-sr" htmlFor="review-q">Search agents</label>
              <input id="review-q" type="search" className="ds-input po-search" placeholder="Search name, account or type" value={q} data-testid="review-search" onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>

          <div className="ds-card po-tbl-card">
            <table className="ds-table po-rt" data-testid="review-table" aria-describedby="rv-cap">
              <caption className="ds-sr" id="rv-cap">Agents found in {p.name}. {visible.length} shown. Rows with a red edge need attention.</caption>
              <colgroup><col className="c-cb" /><col /><col className="c-chg" /><col className="c-own" /><col className="c-tier" /><col className="c-flags" /><col className="c-why" /></colgroup>
              <thead>
                <tr>
                  <th scope="col">{selectableVisible.length > 0 && (
                    <input type="checkbox" data-testid="review-select-all" checked={allOn} aria-label="Select all shown agents that can be imported"
                           onChange={(e) => setSel((s) => { const n = new Set(s); selectableVisible.forEach((r) => (e.target.checked ? n.add(r.id) : n.delete(r.id))); return n })} />
                  )}</th>
                  <th scope="col">Agent</th><th scope="col">Change</th><th scope="col">Owner</th><th scope="col">Tier</th><th scope="col">Risk flags</th>
                  <th scope="col"><span className="ds-sr">Details</span></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => (
                  <Row key={r.id} row={r} provider={p.name} owners={owners} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)}
                       selected={sel.has(r.id)} onSelect={(on) => setSel((s) => { const n = new Set(s); if (on) n.add(r.id); else n.delete(r.id); return n })}
                       onOwner={(o) => setOwner(r, o)} ownerSet={ownerSet.has(r.id)} onUpdated={updated} />
                ))}
                {visible.length === 0 && (
                  <tr className="po-empty-row"><td colSpan={7}>
                    <div className="po-empty" data-testid="review-empty">
                      <h3>No agents match</h3>
                      <p>Try another filter or clear the search.</p>
                      <button type="button" className="ds-btn ds-btn-sm" onClick={() => { setFilter('all'); setQ('') }}>Show all agents</button>
                    </div>
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="po-footer po-footer-sticky" data-testid="review-bulk-bar">
            <div className="po-footer-l">
              <strong data-testid="review-selected-count">{chosen.length} selected</strong>
              <label className="ds-sr" htmlFor="bulk-owner">Assign owner to selected agents</label>
              <select id="bulk-owner" className="po-own" data-testid="review-bulk-owner" disabled={!chosen.length} value="" onChange={(e) => bulkOwner(e.target.value)}>
                <option value="">Assign owner…</option>
                {owners.slice().sort((a, b) => a.localeCompare(b)).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              <button type="button" className="ds-btn ds-btn-sm" data-testid="review-bulk-exclude" disabled={!chosen.length} onClick={() => setSel(new Set())}>Clear selection</button>
              <span className="ds-helper po-flat po-footer-note" id="imp-note">
                {chosen.length === 0 ? 'Select at least one agent to import.' : <>
                  {noOwner > 0 && <>{plural(noOwner, 'agent', 'agents')} without an owner will import as <strong>Needs owner</strong>. </>}
                  {missing > 0 && `${plural(missing, 'missing agent', 'missing agents')} ${missing === 1 ? 'stays' : 'stay'} in ACT. `}
                </>}
              </span>
            </div>
            <div className="po-footer-r">
              <button type="button" className="ds-btn ds-btn-primary" data-testid="review-import" disabled={!chosen.length || importing}
                      aria-busy={importing ? 'true' : undefined} aria-describedby="imp-note" onClick={runImport}>{importing ? 'Importing…' : label}</button>
            </div>
          </div>
          <div className="ds-sr" role="status" aria-live="polite">{announce}</div>
        </>
      )}
    </WizardFrame>
  )
}
