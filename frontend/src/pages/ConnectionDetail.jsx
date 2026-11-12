// Connection detail: status, the awaiting-import queue, access, scope, sync, scan history, and Disconnect.
import React from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { connectionsApi } from '../api'
import { Breadcrumb } from '../components/form'
import { TierBadge } from '../components/ui'
import ScheduleCard, { scheduleText, useSchedule } from '../components/connect/ScheduleCard'
import { ErrorPanel, IconAlert, IconLock, ProviderMark, SampleBadge, StatusPill, useConn, useCopy } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'
import { fmtTs, relTime, sourceLabel } from '../format'

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`
const SCANNABLE = ['trusted', 'verified', 'partial', 'connected']

// The one place a connection's state becomes words. Sample connections never say "Connected".
export function statusOf(conn) {
  const s = conn.state
  if (s === 'disconnected') return { kind: 'idle', text: 'Disconnected' }
  if (s === 'needs_attention') return { kind: 'warn', text: 'Needs attention' }
  if (s === 'failed') return { kind: 'err', text: 'Setup failed' }
  if (s === 'draft' || s === 'waiting') return { kind: 'busy', text: 'Setup not finished' }
  if (s === 'trusted') return { kind: 'info', text: 'Trust detected' }
  if (s === 'partial') return { kind: 'warn', text: 'Degraded' }
  return conn.sample ? { kind: 'info', text: 'Sample tenant' } : { kind: 'ok', text: 'Healthy' }
}

function Fig({ label, children, sub, testid }) {
  return <div><dt>{label}</dt><dd data-testid={testid}>{children}<small>{sub}</small></dd></div>
}

function DisconnectDialog({ dlg, name, agents, sample, onConfirm, busy, error }) {
  return (
    <dialog className="po-dialog" ref={dlg} data-testid="disconnect-dialog" aria-labelledby="dc-h">
      <div className="po-dialog-body">
        <h2 id="dc-h">Disconnect {name}?</h2>
        <p>ACT stops reading from {name} and cancels the sync. {agents ? `The ${plural(agents, 'agent', 'agents')} already in ACT stay` : 'Agents already in ACT stay'}, with their history, and are marked <strong>Source disconnected</strong>.</p>
        {!sample && <p className="ds-helper">The trust you created stays in your {name} account until you delete it there.</p>}
        {error && <p className="po-fielderr" role="alert">{error}</p>}
      </div>
      <div className="po-dialog-foot">
        <button type="button" className="ds-btn" data-testid="disconnect-cancel" onClick={() => dlg.current?.close()}>Keep connection</button>
        <button type="button" className="ds-btn ds-btn-danger" data-testid="disconnect-confirm" disabled={busy} aria-busy={busy ? 'true' : undefined} onClick={onConfirm}>Disconnect {name}</button>
      </div>
    </dialog>
  )
}

export default function ConnectionDetail() {
  const { connId } = useParams()
  const nav = useNavigate()
  const c = useConn(connId)
  const sched = useSchedule(connId)
  const [scans, setScans] = React.useState(null)
  const [aw, setAw] = React.useState({ rows: [], count: 0, scan_id: null })
  const [busy, setBusy] = React.useState('')
  const [err, setErr] = React.useState('')
  const [dcErr, setDcErr] = React.useState('')
  const [checkNote, setCheckNote] = React.useState('')
  const dlg = React.useRef(null)
  const disBtn = React.useRef(null)
  const { copy, note: copyNote } = useCopy()
  const h1 = React.useRef(null)

  React.useEffect(() => {
    let live = true
    connectionsApi.scans(connId).then((r) => live && setScans(r.scans || []), () => live && setScans([]))
    connectionsApi.awaiting(connId).then((r) => live && setAw(r), () => {})
    return () => { live = false }
  }, [connId])
  React.useEffect(() => { window.scrollTo(0, 0); h1.current?.focus({ preventScroll: true }) }, [c.conn?.id])

  const conn = c.conn
  const p = PROVIDERS[conn?.provider]
  if (c.error) return <div className="ds-page ds-container po-page"><ErrorPanel title="ACT couldn't load this connection" onRetry={c.reload}>This is on ACT's side. Try again in a moment.</ErrorPanel></div>
  if (!conn) return <div className="ds-page ds-container po-page" aria-busy="true" data-testid="connection-loading"><span className="ds-skel po-skel-title" /><span className="ds-skel po-skel-row" /><span className="ds-skel po-skel-row" /></div>
  if (!p) return <Navigate to="/connections" replace />

  const st = statusOf(conn)
  const gone = conn.state === 'disconnected'
  const attention = conn.state === 'needs_attention'
  const canScan = SCANNABLE.includes(conn.state)
  const lastScan = (scans || []).find((s) => ['done', 'partial'].includes(s.state))
  const nextScan = sched.saved && sched.schedule === 'daily' && !gone ? `${sched.time}` : gone ? 'Off' : 'Not scheduled'

  const scanNow = async () => {
    setBusy('scan'); setErr('')
    try { const r = await connectionsApi.startScan(connId); nav(`/connections/${connId}/scans/${r.scan_id}`) } catch (e) { setErr(e.message || 'The scan did not start.'); setBusy('') }
  }
  const disconnect = async () => {
    setBusy('disconnect'); setDcErr('')
    try { await connectionsApi.remove(connId); dlg.current?.close(); c.reload(); setBusy(''); requestAnimationFrame(() => h1.current?.focus()) } catch (e) { setDcErr(e.message || "ACT couldn't disconnect."); setBusy('') }
  }
  const checkNow = async () => {
    setBusy('check'); setCheckNote('')
    try { const r = await connectionsApi.check(connId); setCheckNote(r.state === 'verified' || r.state === 'connected' ? 'Access checked: everything ACT needs is in place.' : 'Access checked: something still needs attention.'); c.reload() } catch { setCheckNote("ACT couldn't run the check. Try again in a moment.") } finally { setBusy('') }
  }

  return (
    <div className="ds-page ds-container po-page" data-testid="connection-detail">
      <header className="ds-page-header">
        <Breadcrumb items={[{ label: 'Connections', to: '/connections' }, { label: p.name }]} />
        <div className="ds-page-header-row">
          <div className="po-detail-head">
            <ProviderMark id={conn.provider} large />
            <div className="po-head-text">
              <h1 className="ds-title po-title" ref={h1} tabIndex={-1}>{p.name}{conn.sample ? ' sample tenant' : ''}</h1>
              <p className="ds-description po-products">{p.products}</p>
            </div>
          </div>
          <div className="po-head-actions">
            <button type="button" className="ds-btn ds-btn-primary" data-testid="connection-scan-now" disabled={!canScan || !!busy} aria-describedby={canScan ? undefined : 'scan-dis'} aria-busy={busy === 'scan' ? 'true' : undefined} onClick={scanNow}>Scan now</button>
            {!canScan && <span className="ds-sr" id="scan-dis">{gone ? 'This connection is disconnected.' : 'Scans are paused until the connection is fixed.'}</span>}
            <Link className="ds-btn" to={`/connect/${conn.provider}/${connId}/access`} data-testid="connection-check-access">Check access</Link>
            {gone && <Link className="ds-btn" to={`/connect/${conn.provider}`} data-testid="connection-reconnect">Reconnect</Link>}
          </div>
        </div>
        <div className="po-detail-meta">
          <StatusPill kind={st.kind} testid="connection-status">{st.text}</StatusPill>
          {conn.sample && <SampleBadge />}
          <span className="po-meta-i"><IconLock /> {conn.sample ? 'Fictional tenant: nothing is read from ' + p.name : 'No secrets stored: ' + p.name + ' trusts this Domino sign-in'}</span>
          <span>Connected {fmtTs(conn.created_at).slice(0, 10)}{conn.created_by ? ` by ${conn.created_by}` : ''}</span>
          <span>Sync: {scheduleText(sched)}</span>
        </div>
      </header>

      {err && <div className="ds-alert ds-alert-error" role="alert"><IconAlert /><div><strong>ACT couldn't start the scan.</strong><p>{err}</p></div></div>}
      {checkNote && <div className="ds-alert ds-alert-info" role="status"><div>{checkNote}</div></div>}
      {attention && (
        <div className="ds-alert ds-alert-warn" role="alert" data-testid="connection-attention">
          <IconAlert />
          <div>
            <strong>{p.name} rejected ACT's sign-in or a required permission is missing, so scans are paused.</strong>
            <p>Run the setup again to trust the current Domino identity, then check access. Agents already in ACT are not affected.</p>
            <div className="ds-alert-actions">
              <Link className="ds-btn ds-btn-sm ds-btn-primary" to={`/connect/${conn.provider}`} data-testid="connection-reconnect">Re-run setup</Link>
              <button type="button" className="ds-btn ds-btn-sm" data-testid="connection-copy-error" onClick={() => copy(`${p.name} access check failed for connection ${connId} (state ${conn.state}, last check ${conn.last_check || 'never'}).`)}>Copy error details</button>
              <span className="ds-sr" role="status">{copyNote}</span>
            </div>
          </div>
        </div>
      )}
      {gone && <div className="ds-alert ds-alert-info" role="status"><div><strong>Disconnected.</strong> <span>ACT no longer reads from {p.name}. The agents it found stay in Fleet, marked Source disconnected. Reconnect to scan again.</span></div></div>}

      <dl className="po-kv ds-card po-figs">
        <Fig label="Agents in ACT" sub="from this connection">{conn.agent_count}</Fig>
        <Fig label="Awaiting import" sub={aw.count ? 'found by sync' : 'none'} testid="connection-awaiting-count">{aw.count}</Fig>
        <Fig label="Last scan" sub={lastScan ? `${plural(lastScan.counts?.found || 0, 'agent', 'agents')} found` : 'no scan yet'}>{lastScan ? relTime(lastScan.finished_at || lastScan.started_at) : 'Never'}</Fig>
        <Fig label="Next scan" sub={sched.saved && sched.schedule === 'daily' && !gone ? 'scheduled' : 'no schedule saved'}>{nextScan}</Fig>
      </dl>

      {aw.count > 0 && (
        <section className="ds-card po-mb" aria-labelledby="aw-h" data-testid="awaiting-import-queue">
          <div className="po-panel-head">
            <h2 id="aw-h">Awaiting import</h2>
            <Link className="ds-btn ds-btn-sm" to={`/connections/${connId}/scans/${aw.scan_id}/review`} data-testid="awaiting-review-all">Review all {aw.count}</Link>
          </div>
          <div className="po-tbl-wrap">
            <table className="ds-table po-qt">
              <colgroup><col /><col className="c-where" /><col className="c-sug" /><col className="c-found" /><col className="c-act" /></colgroup>
              <thead><tr><th scope="col">Agent</th><th scope="col">Where</th><th scope="col">Suggested</th><th scope="col">Found</th><th scope="col"><span className="ds-sr">Action</span></th></tr></thead>
              <tbody>
                {aw.rows.map((r, i) => {
                  const found = (scans || []).find((s) => s.scan_id === r.scan_id)
                  return (
                    <tr key={r.id} data-testid={`awaiting-row-${i}`}>
                      <td><span className="po-aname-row"><span className="po-aname" title={r.name}>{r.name}</span>{r.sample && <SampleBadge />}</span><span className="po-asub">{sourceLabel(r.platform)}</span></td>
                      <td className="ds-trunc" title={r.native_id}>{r.native_id}</td>
                      <td>{r.suggestion?.tier != null ? <TierBadge tier={r.suggestion.tier} /> : '—'}</td>
                      <td className="po-muted">{found ? relTime(found.started_at) : '—'}</td>
                      <td><Link className="ds-btn ds-btn-sm" to={`/connections/${connId}/intake/${r.id}`} data-testid={`awaiting-review-${i}`}>Review<span className="ds-sr"> {r.name}</span></Link></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="po-two po-mb">
        <section className="ds-card" aria-labelledby="ac-h">
          <div className="po-panel-head"><h2 id="ac-h">Access</h2><StatusPill kind={st.kind}>{conn.last_check ? `Checked ${relTime(conn.last_check)}` : 'Not checked yet'}</StatusPill></div>
          <div className="po-panel-body">
            <dl className="po-dl">
              <div><dt>Trust</dt><dd>{conn.sample ? 'Fictional sample tenant. There is nothing to authenticate.' : `${p.name} trusts this Domino deployment's sign-in. No passwords, keys or tokens are stored.`}</dd></div>
              {conn.found?.role && <div><dt>Role</dt><dd className="po-mono">{conn.found.role}</dd></div>}
              {conn.found?.account && <div><dt>Account</dt><dd>{conn.found.account}</dd></div>}
              <div><dt>Last checked</dt><dd>{conn.last_check ? fmtTs(conn.last_check) : 'Not yet'}</dd></div>
            </dl>
            {!gone && <button type="button" className="ds-btn ds-btn-sm po-recheck" disabled={busy === 'check'} aria-busy={busy === 'check' ? 'true' : undefined} onClick={checkNow}>Check again</button>}
          </div>
        </section>
        <section className="ds-card" aria-labelledby="sp-h">
          <div className="po-panel-head"><h2 id="sp-h">Scope</h2><Link className="ds-btn ds-btn-sm" to={`/connect/${conn.provider}/${connId}/scope`} data-testid="connection-edit-scope">Edit scope</Link></div>
          <div className="po-panel-body">
            <dl className="po-dl">
              <div><dt>Selected</dt><dd>{conn.scopes.selected.length} of {conn.scopes.total} {conn.scopes.total === 1 ? 'scope' : 'scopes'}</dd></div>
              <div><dt>Products</dt><dd>{p.products}</dd></div>
              <div><dt>Daily sync</dt><dd>{conn.scopes.selected.length ? 'Reads the selected scopes only' : 'Select a scope to scan'}</dd></div>
            </dl>
          </div>
        </section>
      </div>

      {!gone && <ScheduleCard connId={connId} provider={p.name} sample={conn.sample} state={sched} titleId="sy-h" className="po-mb" />}

      <section className="ds-card po-mb" aria-labelledby="hi-h" data-testid="sync-history">
        <div className="po-panel-head"><h2 id="hi-h">Scan history</h2></div>
        <div className="po-tbl-wrap">
          <table className="ds-table po-ht">
            <colgroup><col className="c-start" /><col /><col className="c-n" /><col className="c-n" /><col className="c-n" /><col className="c-n" /><col className="c-res" /></colgroup>
            <thead><tr><th scope="col">Started</th><th scope="col">Trigger</th><th scope="col">Found</th><th scope="col">New</th><th scope="col">Changed</th><th scope="col">Missing</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {scans === null && <tr><td colSpan={7}><span className="ds-skel po-skel-short" /></td></tr>}
              {scans && scans.length === 0 && <tr className="po-empty-row"><td colSpan={7} className="po-muted">No scans yet. Scan now to read this connection.</td></tr>}
              {(scans || []).map((s) => {
                const k = s.counts || {}
                const res = s.state === 'done' ? { kind: 'ok', t: 'Done' } : s.state === 'partial' ? { kind: 'warn', t: 'Some scopes skipped' } : s.state === 'failed' ? { kind: 'err', t: 'Failed' } : { kind: 'busy', t: 'Running' }
                return (
                  <tr key={s.scan_id}>
                    <td><Link to={`/connections/${connId}/scans/${s.scan_id}`} title={fmtTs(s.started_at)}>{relTime(s.started_at)}</Link></td>
                    <td>{s.kind === 'sync' ? 'Scheduled sync' : 'Manual scan'}{s.sample ? ' (sample)' : ''}</td>
                    <td>{k.found ?? '—'}</td><td>{k.new ?? '—'}</td><td>{k.changed ?? '—'}</td><td>{k.missing ?? '—'}</td>
                    <td><StatusPill kind={res.kind}>{res.t}</StatusPill>{s.job_url && <> <a href={s.job_url} target="_blank" rel="noreferrer">Job{s.job_id ? ` #${s.job_id}` : ''}<span className="ds-sr"> (opens in a new tab)</span></a></>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {!gone && (
        <section className="ds-card" aria-labelledby="dz-h">
          <div className="po-panel-head"><h2 id="dz-h">Disconnect</h2></div>
          <div className="po-panel-body po-dz">
            <p className="ds-helper po-flat">Stops all reads from {p.name}. Agents already in ACT stay, marked Source disconnected.{conn.sample ? '' : ` You remove the trust on the ${p.name} side.`}</p>
            <button type="button" className="ds-btn ds-btn-danger" ref={disBtn} data-testid="connection-disconnect" onClick={() => { setDcErr(''); dlg.current?.showModal() }}>Disconnect {p.name}</button>
          </div>
        </section>
      )}
      <DisconnectDialog dlg={dlg} name={p.name} agents={conn.agent_count} sample={conn.sample} onConfirm={disconnect} busy={busy === 'disconnect'} error={dcErr} />
    </div>
  )
}
