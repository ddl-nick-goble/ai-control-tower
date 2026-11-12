// Single-agent intake: the review row's detail as a full page, for one agent waiting in Awaiting import.
import React from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { agentsApi, connectionsApi, scansApi } from '../api'
import { Breadcrumb } from '../components/form'
import { CriteriaList, DiffList, OwnerSelect, TierBlock, TierCell, rowFlags } from '../components/connect/intake'
import { ErrorPanel, SampleBadge, SampleBanner, useConn } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'
import { relTime, sourceLabel } from '../format'

const list = (xs) => (xs || []).map((x) => (typeof x === 'string' ? x : x?.name || JSON.stringify(x)))

export default function Intake() {
  const { connId, stagedId } = useParams()
  const nav = useNavigate()
  const c = useConn(connId)
  const [row, setRow] = React.useState(undefined) // undefined loading, null not in the queue
  const [when, setWhen] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [owners, setOwners] = React.useState([])
  const [ownerSet, setOwnerSet] = React.useState(false)
  const [dismissing, setDismissing] = React.useState(false)
  const [reason, setReason] = React.useState('')
  const [reasonErr, setReasonErr] = React.useState(false)
  const [busy, setBusy] = React.useState('')
  const [fail, setFail] = React.useState('')
  const [rev, setRev] = React.useState(0)
  const h1 = React.useRef(null)

  React.useEffect(() => {
    let live = true
    setError(false)
    Promise.all([connectionsApi.awaiting(connId), connectionsApi.scans(connId).catch(() => ({ scans: [] }))]).then(([aw, sc]) => {
      if (!live) return
      const r = (aw.rows || []).find((x) => String(x.id) === String(stagedId)) || null
      setRow(r)
      const s = (sc.scans || []).find((x) => r && x.scan_id === r.scan_id)
      setWhen(s ? s.started_at : null)
    }, () => live && setError(true))
    agentsApi.list().then((a) => live && setOwners([...new Set((a || []).map((x) => x.owner).filter((o) => o && o !== 'unassigned'))]), () => {})
    return () => { live = false }
  }, [connId, stagedId, rev])
  React.useEffect(() => { if (row) { window.scrollTo(0, 0); h1.current?.focus({ preventScroll: true }) } }, [row?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const p = PROVIDERS[c.conn?.provider]
  if (c.error || error) return <div className="ds-page ds-container po-page"><ErrorPanel title="ACT couldn't load this agent" onRetry={() => { c.reload(); setRev((r) => r + 1) }}>Try again in a moment.</ErrorPanel></div>
  if (c.conn && !p) return <Navigate to="/connections" replace />
  if (!c.conn || row === undefined) return <div className="ds-page ds-container po-page" aria-busy="true"><span className="ds-skel po-skel-title" /><span className="ds-skel po-skel-row" /></div>
  if (row === null) {
    return (
      <div className="ds-page ds-container po-page">
        <div className="ds-card ds-empty" data-testid="intake-gone">
          <h1 className="ds-empty-title">This agent is no longer waiting</h1>
          <p className="ds-helper">It was imported, dismissed or replaced by a newer scan.</p>
          <Link className="ds-btn" to={`/connections/${connId}`}>Back to the {p.name} connection</Link>
        </div>
      </div>
    )
  }

  const flags = rowFlags(row)
  const tools = list(row.summary?.tools), data = list(row.summary?.data_scopes)
  const ident = row.summary?.identity
  const setOwner = async (owner) => {
    const prev = row.owner
    setRow({ ...row, owner: owner || null }); setOwnerSet(true)
    try { await scansApi.patch(row.id, { owner }) } catch { setRow((r) => ({ ...r, owner: prev })); setFail("ACT couldn't save the owner. Try again.") }
  }
  const importIt = async () => {
    setBusy('import'); setFail('')
    try { const out = await scansApi.importRows(row.scan_id, [row.id]); nav(out.agent_ids?.[0] ? `/agents/${out.agent_ids[0]}` : `/connections/${connId}`) } catch (e) { setFail(e.message || "ACT couldn't import this agent."); setBusy('') }
  }
  const dismiss = async () => {
    if (reason.trim().length < 10) { setReasonErr(true); return }
    setBusy('dismiss'); setFail('')
    try { await scansApi.dismiss(row.id, reason.trim()); nav(`/connections/${connId}`) } catch (e) { setFail(e.message || "ACT couldn't dismiss this agent."); setBusy('') }
  }

  return (
    <div className="ds-page ds-container po-page" data-testid="intake-page">
      <header className="ds-page-header">
        <Breadcrumb items={[{ label: 'Connections', to: '/connections' }, { label: p.name, to: `/connections/${connId}` }, { label: row.name }]} />
        <div className="ds-page-header-row">
          <div className="po-head-text">
            <h1 className="ds-title po-title" ref={h1} tabIndex={-1} title={row.name}>{row.name}</h1>
            <div className="po-detail-meta">
              <span className="ds-chip-tag">{sourceLabel(row.platform)}</span>
              {row.sample && <SampleBadge />}
              <span title={row.native_id} className="po-mono po-trunc">{row.native_id}</span>
              <span>Found by sync {when ? relTime(when) : 'recently'}</span>
            </div>
          </div>
        </div>
      </header>
      {row.sample && <SampleBanner provider={p.name} review />}
      {fail && <div className="ds-alert ds-alert-error" role="alert"><div><strong>That didn't work.</strong><p>{fail}</p></div></div>}

      <div className="po-intake">
        <section className="ds-card" aria-labelledby="in-why">
          <div className="po-panel-head"><h2 id="in-why">Why Tier {row.suggestion?.suggested_tier ?? row.suggestion?.tier} is suggested</h2></div>
          <div className="po-panel-body">
            <CriteriaList row={row} />
            <p className="ds-helper">Scores run from 1 (low) to 5 (high), from the agent's tools, policies and tags. The weighted score sets the tier.</p>
          </div>
        </section>

        <div className="po-intake-side">
          <section className="ds-card" aria-labelledby="in-tier">
            <div className="po-panel-head"><h2 id="in-tier">Tier and owner</h2></div>
            <div className="po-panel-body po-xp-solo">
              <div className="po-tierrow"><TierCell row={row} /></div>
              {row.class === 'changed' && row.diff && <DiffList row={row} testid="review-diff-intake" />}
              <TierBlock row={row} canEdit onUpdated={(r) => setRow((old) => ({ ...old, ...r }))} />
              <h4>Owner</h4>
              <OwnerSelect row={row} owners={owners} label={`Owner for ${row.name}`} setByYou={ownerSet} onChange={setOwner} />
            </div>
          </section>
          <section className="ds-card" aria-labelledby="in-facts">
            <div className="po-panel-head"><h2 id="in-facts">What ACT found</h2></div>
            <div className="po-panel-body">
              <dl className="po-dl">
                {row.summary?.description && <div><dt>Description</dt><dd>{row.summary.description}</dd></div>}
                <div><dt>Tools</dt><dd>{tools.length ? tools.join(', ') : 'None found'}</dd></div>
                <div><dt>Data scopes</dt><dd>{data.length ? data.join(', ') : 'None found'}</dd></div>
                <div><dt>Identity</dt><dd>{ident ? `${ident.type || 'unknown'}${ident.distinct === false ? ' (shared)' : ''}${ident.subject ? ` · ${ident.subject}` : ''}` : 'Unknown'}</dd></div>
                <div><dt>Risk flags</dt><dd>{flags.length ? <span className="po-flags">{flags.map((f) => <span key={f.key} className={`po-flag${f.high ? ' po-flag-high' : ''}`}>{f.label}</span>)}</span> : 'None'}</dd></div>
              </dl>
            </div>
          </section>
        </div>
      </div>

      <div className="po-footer po-footer-sticky">
        <div className="po-footer-l">
          {dismissing ? (
            <div className="po-dismiss">
              <label className="ds-label" htmlFor="dismiss-reason">Why dismiss this agent? <span className="po-opt">(required, audited)</span></label>
              <input id="dismiss-reason" className="ds-input" data-testid="intake-dismiss-reason" value={reason} autoComplete="off" aria-invalid={reasonErr ? 'true' : undefined} aria-describedby="dismiss-err"
                     placeholder="For example: a test agent that is not in scope" onChange={(e) => { setReason(e.target.value); setReasonErr(false) }} />
              <p className="po-fielderr" id="dismiss-err" role={reasonErr ? 'alert' : undefined}>{reasonErr ? 'Enter a reason of at least 10 characters. ACT records it in the audit log.' : ''}</p>
            </div>
          ) : <span className="ds-helper po-flat">Imported agents start in Pending review. Dismissed agents leave the queue.</span>}
        </div>
        <div className="po-footer-r">
          {dismissing
            ? <>
                <button type="button" className="ds-btn" onClick={() => { setDismissing(false); setReasonErr(false) }}>Cancel</button>
                <button type="button" className="ds-btn ds-btn-danger" disabled={busy === 'dismiss'} onClick={dismiss}>Dismiss agent</button>
              </>
            : <>
                <button type="button" className="ds-btn" data-testid="intake-dismiss" onClick={() => setDismissing(true)}>Dismiss</button>
                <button type="button" className="ds-btn ds-btn-primary" data-testid="intake-import" disabled={busy === 'import'} aria-busy={busy === 'import' ? 'true' : undefined} onClick={importIt}>{row.sample ? 'Import agent as sample data' : 'Import agent'}</button>
              </>}
        </div>
      </div>
    </div>
  )
}
