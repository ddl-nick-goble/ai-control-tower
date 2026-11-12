// Step 4: Scan. Live counters, scopes and the agents found, from GET /api/scans/:id polled every 2 s. The scan runs on the
// server (a Domino Job, or in process for sample data), so closing the page never stops it and a reload restores it.
import React from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { connectionsApi, scansApi } from '../api'
import { Alert } from '../components/form'
import { TierBadge } from '../components/ui'
import WizardFrame from '../components/connect/WizardFrame'
import { ErrorPanel, IconAlert, IconClock, IconDash, IconCheck, IconInfo, SampleBadge, SampleBanner, Spinner, useConn } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'
import { sourceLabel } from '../format'

const POLL_MS = 2000
const LIVE_MS = 5000 // the polite announcement says at most one thing every 5 s
const TERMINAL = ['done', 'partial', 'failed']
const reduced = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const scopeDone = (p) => ['done', 'failed', 'skipped'].includes(p.state)
const errorsOf = (p) => (Array.isArray(p.errors) ? p.errors : p.errors ? [`${p.errors} errors`] : [])
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

function duration(a, b) {
  const s = Math.max(0, Math.round((new Date(b) - new Date(a)) / 1000))
  if (!Number.isFinite(s)) return ''
  if (s < 1) return 'under a second'
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`
}

// Starting point of the wizard's step 3 Continue: start the scan, then land on its page (the URL a reload can restore).
export function ScanStart() {
  const { provider, connId } = useParams()
  const nav = useNavigate()
  const [error, setError] = React.useState('')
  const [rev, setRev] = React.useState(0)
  const started = React.useRef(-1)
  React.useEffect(() => {
    if (started.current === rev) return
    started.current = rev
    setError('')
    connectionsApi.startScan(connId).then((r) => nav(`/connections/${connId}/scans/${r.scan_id}`, { replace: true }), (e) => setError(e.message || 'The scan did not start.'))
  }, [rev, connId, nav])
  if (!PROVIDERS[provider]) return <Navigate to="/connections" replace />
  return (
    <WizardFrame provider={provider} connId={connId} step={4} title={`Scan ${PROVIDERS[provider].name}`} hideAside
                 description="ACT reads each agent's tools, identity, data access and owner tags. Nothing is imported until you review.">
      {error
        ? <ErrorPanel title="ACT couldn't start the scan" retryTestid="scan-retry" onRetry={() => setRev((r) => r + 1)}>{error}</ErrorPanel>
        : <section className="ds-card po-panel" aria-busy="true"><div className="po-panel-body"><span className="ds-skel po-skel-row" /><span className="ds-skel po-skel-row" /></div></section>}
    </WizardFrame>
  )
}

function useScan(scanId) {
  const [scan, setScan] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [rev, setRev] = React.useState(0)
  const first = React.useRef(null) // whether the first answer was already finished (a reload) or still running
  React.useEffect(() => {
    let live = true, timer
    first.current = null
    setError(false)
    const tick = async () => {
      try {
        const s = await scansApi.get(scanId)
        if (!live) return
        if (first.current == null) first.current = TERMINAL.includes(s.state) ? 'done' : 'live'
        setScan(s); setError(false)
        if (!TERMINAL.includes(s.state)) timer = setTimeout(tick, POLL_MS)
      } catch {
        if (!live) return
        setError(true)
        timer = setTimeout(tick, POLL_MS * 2) // keep trying: the page recovers on its own
      }
    }
    tick()
    return () => { live = false; clearTimeout(timer) }
  }, [scanId, rev])
  return { scan, error, retry: () => { setScan(null); first.current = null; setRev((r) => r + 1) }, restored: first.current === 'done' }
}

// A value that moves at most once per `ms`, so a polite live region is not spoken every poll. `now` jumps straight through.
function useThrottled(value, ms, now) {
  const [v, setV] = React.useState(value)
  const last = React.useRef(0)
  const latest = React.useRef(value)
  latest.current = value
  React.useEffect(() => {
    if (now) { setV(value); last.current = Date.now(); return undefined }
    const wait = Math.max(0, ms - (Date.now() - last.current))
    const t = setTimeout(() => { last.current = Date.now(); setV(latest.current) }, wait)
    return () => clearTimeout(t)
  }, [value, now, ms])
  return v
}

function Counter({ k, label, n }) {
  return (
    <div className="ds-card po-stat">
      <span>{label}</span>
      <strong data-testid={`scan-counter-${k}`}>{n}</strong>
    </div>
  )
}

function ScopeRow({ p }) {
  const errs = errorsOf(p)
  let icon, state
  if (p.state === 'queued') { icon = <IconClock />; state = 'Queued' }
  else if (p.state === 'running') { icon = <Spinner />; state = p.message || 'Reading…' }
  else if (p.state === 'done') { icon = <span className="po-ok"><IconCheck /></span>; state = `Done · ${plural(p.found || 0, 'agent', 'agents')}` }
  else { icon = <IconDash />; state = `Skipped: ${errs[0] || 'ACT could not read this scope'}` }
  return (
    <li data-testid={`scan-scope-row-${p.scope}`}>
      <span className="po-sico" aria-hidden="true">{icon}</span>
      <span className="po-sname" title={p.scope}>{p.scope}</span>
      <span className={`po-sstate${p.state === 'failed' || p.state === 'skipped' ? ' is-skip' : ''}`} title={state}>{state}</span>
    </li>
  )
}

const CHG = { new: 'New', changed: 'Changed', unchanged: 'Unchanged' }

// The agents found. They come from the staged results; on a live scan they arrive one after another, newest first.
function Feed({ scanId, state, found, animate }) {
  const [rows, setRows] = React.useState([])
  const [shown, setShown] = React.useState(0)
  const [loaded, setLoaded] = React.useState(false)
  const seen = React.useRef(-1)
  React.useEffect(() => {
    if (seen.current === found + state) return undefined
    if (!found && !TERMINAL.includes(state)) return undefined
    seen.current = found + state
    let live = true
    scansApi.staged(scanId).then((r) => { if (live) { setRows((r.rows || []).filter((x) => x.class !== 'missing')); setLoaded(true) } }, () => {})
    return () => { live = false }
  }, [scanId, found, state])
  React.useEffect(() => {
    if (shown >= rows.length) return undefined
    if (!animate || reduced() || TERMINAL.includes(state)) { setShown(rows.length); return undefined }
    const t = setTimeout(() => setShown((n) => n + 1), 110)
    return () => clearTimeout(t)
  }, [rows, shown, animate, state])
  const visible = rows.slice(0, shown).reverse()
  return (
    <ul className="po-feed" data-testid="scan-feed" aria-label="Agents found, newest first">
      {visible.length === 0 && <li className="po-feed-empty"><span className="ds-helper po-flat">{TERMINAL.includes(state) ? (rows.length || (found && !loaded) ? 'Loading the agents found.' : 'This scan found no agents.') : 'Agents appear here as each scope is read.'}</span></li>}
      {visible.map((r) => (
        <li key={r.id} data-testid={`scan-agent-row-${r.native_id}`}>
          <span className="po-fname" title={r.name}>{r.name}</span>
          <span className="po-fright">{r.sample && <SampleBadge />}<span className={`po-chg po-chg-${r.class}`}>{CHG[r.class] || r.class}</span></span>
          <span className="po-fsub" title={r.native_id}>{sourceLabel(r.platform)} · {r.native_id}</span>
          <span className="po-fright">{r.suggestion?.tier != null && <TierBadge tier={r.suggestion.tier} />}</span>
        </li>
      ))}
    </ul>
  )
}

export default function ScanView() {
  const { connId, scanId } = useParams()
  const nav = useNavigate()
  const c = useConn(connId)
  const { scan, error, retry, restored } = useScan(scanId)
  const [retrying, setRetrying] = React.useState(false)
  const [retryErr, setRetryErr] = React.useState('')
  const provider = c.conn?.provider
  const p = PROVIDERS[provider]

  const counts = scan?.counts || {}
  const progress = scan?.progress || []
  const state = scan?.state
  const terminal = TERMINAL.includes(state)
  const done = progress.filter(scopeDone).length
  const skipped = progress.filter((x) => x.state === 'failed' || x.state === 'skipped').length
  const sample = !!(scan?.sample ?? c.conn?.sample)
  const found = counts.found || 0
  const reviewable = (state === 'done' || state === 'partial' || (state === 'failed' && found > 0))
  const total = found + (counts.missing || 0)

  const sentence = React.useMemo(() => {
    if (!scan) return 'Starting the scan…'
    if (state === 'queued') return sample ? 'Starting the sample scan…' : 'Starting the scan job…'
    if (state === 'running') return `Scanning · ${done} of ${progress.length || 1} ${progress.length === 1 ? 'scope' : 'scopes'} done · ${plural(found, 'agent', 'agents')} found so far.`
    if (state === 'failed') return 'The scan stopped before it finished.'
    const t = scan.finished_at ? `Scan complete in ${duration(scan.started_at, scan.finished_at)}.` : 'Scan complete.'
    return `${t} ${found} found, ${counts.missing || 0} missing${skipped ? `, ${plural(skipped, 'scope', 'scopes')} skipped` : ''}.`
  }, [scan, state, done, found, counts.missing, progress.length, skipped])
  const live = useThrottled(sentence, LIVE_MS, terminal || !scan)

  const rerun = async () => {
    setRetrying(true); setRetryErr('')
    try { const r = await connectionsApi.startScan(connId); nav(`/connections/${connId}/scans/${r.scan_id}`) } catch (e) { setRetryErr(e.message || 'The scan did not start.') } finally { setRetrying(false) }
  }

  if (c.error) return <div className="ds-page ds-container po-page"><ErrorPanel title="ACT couldn't load this connection" onRetry={c.reload}>Try again in a moment. Your scan keeps running on the server.</ErrorPanel></div>
  if (!c.conn) {
    return (
      <div className="ds-page ds-container po-page" aria-busy="true">
        <span className="ds-skel po-skel-title" /><span className="ds-skel po-skel-row" /><span className="ds-skel po-skel-row" />
      </div>
    )
  }
  if (!p) return <Navigate to="/connections" replace />

  const noun = p.leaf === 'Region' ? 'scope' : p.leaf
  const jobLine = sample
    ? <>This is a sample scan (<Link to={`/connections/${connId}`} data-testid="scan-job-link">{String(scanId).slice(0, 10)}</Link>). It runs inside ACT, so there is no Domino Job. You can leave this page; the result waits on the {p.name} connection.</>
    : <>Running as Domino Job {scan?.job_url
        ? <a href={scan.job_url} target="_blank" rel="noreferrer" data-testid="scan-job-link">#{scan.job_id || 'open'}<span className="ds-sr"> (opens in a new tab)</span></a>
        : <Link to={`/connections/${connId}`} data-testid="scan-job-link">#{scan?.job_id || String(scanId).slice(0, 8)}</Link>}. You can close this page; ACT keeps scanning and the result waits on the {p.name} connection.</>

  return (
    <WizardFrame provider={provider} connId={connId} step={4} title={`Scan ${p.name}`} hideAside
                 crumbs={[{ label: 'Connections', to: '/connections' }, { label: p.name, to: `/connections/${connId}` }, { label: 'Scan' }]}
                 hrefs={{ 3: `/connect/${provider}/${connId}/scope` }}
                 description="ACT is reading each agent's tools, identity, data access and owner tags. Nothing is imported until you review.">
      {sample && <SampleBanner provider={p.name} />}
      {error && !scan && (
        <ErrorPanel title="ACT couldn't read this scan" testid="scan-error" retryTestid="scan-retry" onRetry={retry}>
          This is on ACT's side. The scan itself keeps running. Try again in a moment.
        </ErrorPanel>
      )}
      {!error && !scan && (
        <section className="ds-card po-panel" aria-busy="true" data-testid="scan-loading"><div className="po-panel-body"><span className="ds-skel po-skel-row" /><span className="ds-skel po-skel-row" /></div></section>
      )}
      {scan && (
        <div data-testid="scan-view" className="po-scan">
          <section className="ds-card po-scan-top" aria-labelledby="scan-h">
            <div className="po-scan-head">
              <h2 id="scan-h" className="po-scan-title">{p.name}{sample ? '' : ` · ${plural(progress.length, noun, `${noun}s`)}`} {sample && <SampleBadge />}</h2>
              <span className={`po-scan-status${state === 'done' || state === 'partial' ? ' is-done' : ''}`} data-testid="scan-status" role="status" aria-live="polite" aria-atomic="true">
                {state === 'running' || state === 'queued' ? <Spinner /> : state === 'failed' ? <IconAlert /> : <IconCheck />}
                <span>{live}</span>
              </span>
            </div>
            <div className={`po-progress${terminal ? ' is-done' : ''}`} role="progressbar" aria-label="Scan progress" aria-valuemin="0" aria-valuemax={Math.max(progress.length, 1)} aria-valuenow={done} data-testid="scan-progress">
              <i style={{ width: `${terminal ? 100 : progress.length ? Math.round((done / progress.length) * 100) : 4}%` }} />
            </div>
          </section>

          {state === 'failed' && (
            <div className="ds-alert ds-alert-error" role="alert" data-testid="scan-error">
              <IconAlert />
              <div>
                <strong>The scan stopped before it finished.</strong>
                <p>{scan.error ? `${scan.error} ` : ''}{found > 0 ? `${plural(found, 'agent', 'agents')} found so far ${found === 1 ? 'is' : 'are'} kept. ` : ''}Retry the scan; nothing was imported.</p>
                {retryErr && <p>{retryErr}</p>}
                <div className="ds-alert-actions">
                  <button type="button" className="ds-btn ds-btn-sm ds-btn-primary" data-testid="scan-retry" disabled={retrying} onClick={rerun}>Retry scan</button>
                  {scan.job_url && <a className="ds-btn ds-btn-sm" href={scan.job_url} target="_blank" rel="noreferrer">View job log<span className="ds-sr"> (opens in a new tab)</span></a>}
                </div>
              </div>
            </div>
          )}
          {state === 'partial' && (
            <div className="ds-alert ds-alert-warn" role="status"><IconAlert /><div><strong>{plural(skipped, 'scope was', 'scopes were')} skipped.</strong><p>The rest finished. Each skipped scope shows its reason below; retry the scan after fixing it.</p></div></div>
          )}

          <div className="po-stats">
            <Counter k="found" label="Agents found" n={counts.found || 0} />
            <Counter k="new" label="New" n={counts.new || 0} />
            <Counter k="changed" label="Changed" n={counts.changed || 0} />
            <Counter k="unchanged" label="Unchanged" n={counts.unchanged || 0} />
            <Counter k="missing" label="Missing" n={counts.missing || 0} />
            <Counter k="skipped" label="Skipped scopes" n={skipped} />
          </div>

          <div className="po-scan-cols">
            <section className="ds-card" aria-labelledby="sl-h">
              <div className="po-panel-head"><h2 id="sl-h">Scopes</h2><span className="ds-helper po-flat">{done} of {progress.length}</span></div>
              {progress.length
                ? <ul className="po-scopes-l">{progress.map((x) => <ScopeRow key={x.scope} p={x} />)}</ul>
                : <p className="po-feed-empty ds-helper po-flat">Scopes appear when the scan starts reading.</p>}
            </section>
            <section className="ds-card" aria-labelledby="fd-h">
              <div className="po-panel-head"><h2 id="fd-h">Agents found</h2><span className="ds-helper po-flat">Newest first</span></div>
              <Feed scanId={scanId} state={state} found={counts.found || 0} animate={!restored} />
            </section>
          </div>

          <div className="po-job" data-testid="scan-job">
            <span className="po-job-text"><IconInfo /><span>{jobLine}</span></span>
            <span className="po-job-actions">
              <Link className="ds-btn ds-btn-sm" to={`/connections/${connId}`} data-testid="scan-leave">Leave and notify me</Link>
              <button type="button" className="ds-btn ds-btn-primary ds-btn-sm" data-testid="scan-review" disabled={!reviewable}
                      aria-describedby={reviewable ? undefined : 'scan-review-why'} onClick={() => nav(`/connections/${connId}/scans/${scanId}/review`)}>
                {reviewable ? `Review ${plural(total, 'agent', 'agents')}` : 'Review agents'}
              </button>
              {!reviewable && <span className="po-job-why" id="scan-review-why">{state === 'failed' ? 'Nothing was found to review.' : 'Review opens when the scan finishes.'}</span>}
            </span>
          </div>
        </div>
      )}
    </WizardFrame>
  )
}
