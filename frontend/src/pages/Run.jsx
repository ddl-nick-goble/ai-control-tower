import React from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { agentPath, clean, fmtJson, fmtMessages, fmtTs, num } from '../format'
import { Breadcrumb } from '../components/form'
import { useAsync } from '../components/ui'
import { Arrow, CopyButton, ErrorPage } from '../components/agentui'

// Never show provider/model ids from trace data.
function label(ev) {
  if (ev.kind === 'model_call') {
    const a = ev.attrs || {}
    const mock = String(a['act.llm.provider'] || '').toLowerCase() === 'mock'
      || /mock-llm/i.test(`${ev.name || ''} ${a['gen_ai.request.model'] || ''}`)
    return mock ? 'mock LLM (demo, no gateway call)' : 'frontier model via gateway'
  }
  return clean(ev.name)
}

const nf = (n) => Number(n).toLocaleString('en-US')

const REASONS = {
  tool_not_in_manifest: 'Outside manifest',
  value_limit_exceeded: 'Over limit',
  value_unverifiable: 'Unverifiable value',
}

const KINDS = {
  input: 'Input', output: 'Output', model_call: 'Model call', tool_call: 'Tool call',
  approval: 'Approval', override: 'Override',
}
const kindLabel = (k) => KINDS[k] || clean(String(k || 'Event').replace(/_/g, ' ')).replace(/^./, (c) => c.toUpperCase())

const FILTERS = [['all', 'All'], ['input', 'Input'], ['model_call', 'Model call'], ['tool_call', 'Tool call'], ['violation', 'Violation']]
const FILTER_NAME = { input: 'input', model_call: 'model call', tool_call: 'tool call', violation: 'violation' }

function content(ev) {
  const a = ev.attrs || {}
  const keys = ev.kind === 'output'
    ? ['gen_ai.output.messages', 'output.value', 'gen_ai.input.messages', 'input.value']
    : ['gen_ai.input.messages', 'input.value', 'gen_ai.output.messages', 'output.value']
  if (ev.content) return fmtMessages(ev.content)
  for (const k of keys) if (a[k]) return fmtMessages(a[k])
  return ''
}

const isoTime = (ts) => { const d = new Date(ts); return isNaN(d) ? '' : d.toISOString().slice(11, 19) }

function fmtRange(a, b) {
  if (!a) return '-'
  const da = new Date(a), db = b ? new Date(b) : null
  if (isNaN(da)) return String(a)
  const start = fmtTs(a).replace(' UTC', '')
  if (!db || isNaN(db)) return `${start} UTC`
  const sameDay = da.toISOString().slice(0, 10) === db.toISOString().slice(0, 10)
  const end = sameDay ? db.toISOString().slice(11, 16) : fmtTs(b).replace(' UTC', '')
  const sec = Math.max(0, Math.round((db - da) / 1000))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  const dur = h ? `${h}h ${m}m` : m ? `${m}m${s ? ` ${s}s` : ''}` : `${s}s`
  return `${start} to ${end} UTC · ${dur}`
}

function Icon({ kind, violation }) {
  const p = { width: 12, height: 12, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  if (violation) return <svg {...p}><path d="M8 2.5l6 10.5H2L8 2.5z" /><path d="M8 7v3M8 11.8v.2" /></svg>
  if (kind === 'model_call') return <svg {...p}><rect x="3.5" y="3.5" width="9" height="9" rx="2" /><path d="M6 1.8v1.7M10 1.8v1.7M6 12.5v1.7M10 12.5v1.7M1.8 6h1.7M1.8 10h1.7M12.5 6h1.7M12.5 10h1.7" /></svg>
  if (kind === 'tool_call') return <svg {...p}><path d="M10.5 2.5a3.2 3.2 0 0 0-3 4.3L2.7 11.6a1.4 1.4 0 0 0 2 2l4.8-4.8a3.2 3.2 0 0 0 4.3-3l-2 2-1.9-.5-.5-1.9 2-2z" /></svg>
  if (kind === 'output') return <svg {...p}><path d="M2.5 8h9M8 4.5L11.5 8 8 11.5M13.5 3v10" /></svg>
  if (kind === 'approval' || kind === 'override') return <svg {...p}><path d="M3 8.5l3.2 3.2L13 4.8" /></svg>
  return <svg {...p}><path d="M13.5 8h-9M8 4.5L4.5 8 8 11.5M2.5 3v10" /></svg>
}

const COLLAPSE_AT = 6
const COLLAPSE_CHARS = 400
const DISPLAY_MAX = 20000

const compactJson = (v) => (v === null || typeof v !== 'object' ? JSON.stringify(v)
  : Array.isArray(v) ? `[${v.map(compactJson).join(', ')}]`
    : `{${Object.entries(v).map(([k, x]) => `${JSON.stringify(k)}: ${compactJson(x)}`).join(', ')}}`)

// Short JSON payloads render on one line; everything else keeps its own formatting.
function payloadText(v) {
  let obj = v
  if (typeof v === 'string' && /^\s*[[{]/.test(v)) { try { obj = JSON.parse(v) } catch { obj = null } }
  if (obj && typeof obj === 'object') {
    const c = compactJson(obj)
    if (c.length <= 90) return clean(c)
  }
  return clean(fmtJson(v))
}

function Payload({ full, testid, what }) {
  const id = React.useId()
  const shown = full.length > DISPLAY_MAX ? full.slice(0, DISPLAY_MAX) + '...' : full
  const lines = shown.split('\n').length
  const long = lines > COLLAPSE_AT || shown.length > COLLAPSE_CHARS
  const [open, setOpen] = React.useState(false)
  const collapsed = long && !open
  const more = lines > COLLAPSE_AT ? `Show ${lines - COLLAPSE_AT} more lines` : 'Show more'
  return (
    <>
      <div className="rt-pl" data-testid={testid} data-collapsed={collapsed ? 'true' : 'false'}>
        <pre id={id} className="rt-pre" data-testid="timeline-payload" data-collapsed={collapsed ? 'true' : 'false'}>{shown}</pre>
        <span className="rt-pl-copy"><CopyButton value={full} label={what} /></span>
      </div>
      {long && (
        <button type="button" className="ds-btn ds-btn-ghost ag-sm rt-toggle" data-testid="payload-toggle" aria-expanded={open} aria-controls={id}
                onClick={() => setOpen((o) => !o)}>{open ? 'Show less' : more}</button>
      )}
    </>
  )
}

function Loading() {
  return (
    <div className="ds-page ds-container" role="status" aria-busy="true" data-testid="timeline-loading">
      <span className="ds-sr">Loading timeline ...</span>
      <span className="ds-skel" style={{ width: 240, height: 14, marginBottom: 16 }} />
      <span className="ds-skel" style={{ width: 280, height: 36, marginBottom: 16 }} />
      <span className="ds-skel" style={{ width: '100%', maxWidth: 640, height: 40, marginBottom: 24 }} />
      <span className="ds-skel" style={{ width: 360, height: 32, marginBottom: 16 }} />
      {Array.from({ length: 8 }, (_, i) => <span key={i} className="ds-skel" style={{ height: 28, marginBottom: 12, width: `${96 - (i % 3) * 8}%` }} />)}
    </div>
  )
}

export default function Run() {
  const { runId } = useParams()
  return <RunView key={runId} runId={runId} />
}

function RunView({ runId }) {
  const [rev, setRev] = React.useState(0)
  const [params, setParams] = useSearchParams()
  const tl = useAsync(() => api.timeline(runId), [runId, rev])
  const agentId = tl.data?.agent_id
  const fnd = useAsync(() => (agentId ? api.findings(agentId).then((items) => ({ agentId, items })) : Promise.resolve({ agentId: null, items: [] })), [agentId])
  const agentInfo = useAsync(() => (agentId ? api.agent(agentId).catch(() => null) : Promise.resolve(null)), [agentId])
  const [filter, setFilter] = React.useState('all')
  const [flash, setFlash] = React.useState(null)
  const spanParam = params.get('span')
  const [missingSpan, setMissingSpan] = React.useState(false)
  const agentName = clean(agentInfo.data?.name) || 'Agent'
  const crumbs = [{ label: 'Fleet', to: '/' },
    ...(agentId ? [{ label: agentName, to: agentPath(agentId) }] : []),
    { label: `Run ${String(runId).slice(0, 8)}` }]
  const findingsPending = tl.loading || !tl.data || (!fnd.error && (fnd.loading || fnd.data?.agentId !== (agentId ?? null)))
  const findingsFailed = !!fnd.error

  const violations = React.useMemo(() => {
    const m = new Map()
    for (const f of fnd.data?.items || []) {
      if (f.run_id !== runId || f.status !== 'open') continue
      for (const v of f.detail?.violations || []) if (v.span_id) m.set(v.span_id, { ...v, reason: v.reason || f.reason || 'tool_not_in_manifest', findingId: f.id })
      if (f.span_id && !m.has(f.span_id)) m.set(f.span_id, { reason: f.reason || 'tool_not_in_manifest', tool: f.tool, limit: f.limit, observed_value: f.observed_value, findingId: f.id })
    }
    return m
  }, [fnd.data, runId])

  const findingIds = React.useMemo(() => new Set((fnd.data?.items || []).map((f) => f.id)), [fnd.data])
  const ready = !tl.error && !findingsPending
  const events = ready ? tl.data.events : []
  const rows = React.useMemo(() => events.map((ev, i) => {
    const v = violations.get(ev.span_id)
    return { ev, i, v: v && ev.kind === 'tool_call' ? v : null }
  }), [events, violations])
  const counts = React.useMemo(() => ({
    all: rows.length,
    input: rows.filter((r) => r.ev.kind === 'input' || r.ev.kind === 'output').length,
    model_call: rows.filter((r) => r.ev.kind === 'model_call').length,
    tool_call: rows.filter((r) => r.ev.kind === 'tool_call').length,
    violation: rows.filter((r) => r.v).length,
  }), [rows])
  const shown = rows.filter((r) => filter === 'all' || (filter === 'violation' ? r.v : filter === 'input' ? (r.ev.kind === 'input' || r.ev.kind === 'output') : r.ev.kind === filter))

  // Arriving with ?span= (from a finding): scroll to the event, focus it, flash it, then drop the parameter.
  React.useEffect(() => {
    if (!ready || !spanParam) return
    setFilter('all')
    const id = requestAnimationFrame(() => {
      const el = Array.from(document.querySelectorAll('[data-testid="timeline-event"]')).find((e) => e.getAttribute('data-span-id') === spanParam)
      if (el) {
        el.scrollIntoView({ block: 'center' })
        el.focus({ preventScroll: true })
        setFlash(spanParam)
      } else setMissingSpan(true)
      setParams((cur) => { const n = new URLSearchParams(cur); n.delete('span'); return n }, { replace: true })
    })
    return () => cancelAnimationFrame(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, spanParam])
  React.useEffect(() => {
    if (!flash) return
    const t = setTimeout(() => setFlash(null), 2600)
    return () => clearTimeout(t)
  }, [flash])

  if (tl.error) {
    return <ErrorPage title="We could not load this run" message={`${tl.error.status ? tl.error.status + ': ' : ''}${clean(tl.error.message)}`}
      onRetry={() => setRev((r) => r + 1)} back={<Link className="ds-btn ds-btn-ghost" to="/">Back to Fleet</Link>} />
  }
  if (findingsPending) return <Loading />

  const t = tl.data
  const first = events[0]?.ts, last = events[events.length - 1]?.ts
  const nViol = counts.violation
  const clearFilter = <button type="button" className="ds-btn ds-btn-ghost ag-sm" onClick={() => setFilter('all')}>Clear filter</button>

  return (
    <div className="ds-page ds-container" data-testid="timeline-page" data-event-count={events.length}>
      <Breadcrumb items={crumbs} />
      <header className="ds-page-header rt-header" data-testid="run-header">
        <h1 className="ds-title">Run timeline</h1>
        <dl className="rt-meta">
          <div><dt>Run</dt><dd>
            <span className="ag-idv">
              <span className="ds-trunc ag-idv-text ag-mono" data-testid="run-id" title={runId}>{runId}</span>
              <CopyButton value={runId} label="run ID" />
            </span></dd></div>
          <div><dt>Agent</dt><dd>{agentId
            ? <Link className="ds-trunc rt-agent" to={agentPath(agentId)} title={agentName}>{agentName}</Link> : <span className="ag-none">—</span>}</dd></div>
          <div><dt>Time range</dt><dd className="ds-nowrap ag-ink" data-testid="run-range">{fmtRange(t.started_at || first, t.ended_at || last)}</dd></div>
          <div><dt>Events</dt><dd className="ds-nowrap ag-ink" data-testid="run-event-count">
            {nf(events.length)} {events.length === 1 ? 'event' : 'events'}
            {nViol > 0 && <> · <span className="rt-viol-count">{nf(nViol)} {nViol === 1 ? 'violation' : 'violations'}</span></>}</dd></div>
        </dl>
      </header>
      {missingSpan && <div className="ds-alert rt-alert" role="status" data-testid="span-missing">The event linked from the finding is not in this run's timeline.</div>}
      {findingsFailed && <div className="ds-alert ds-alert-error rt-alert" role="alert" data-testid="findings-error">Could not load findings, so violations are not highlighted on this timeline.</div>}
      {events.length > 0 && (
        <div className="rt-toolbar">
          <div className="rt-seg" role="group" aria-label="Filter by event type">
            {FILTERS.map(([k, name]) => (
              <button key={k} type="button" className="rt-seg-btn" aria-pressed={filter === k} onClick={() => setFilter(k)}>
                {name}<span className="rt-n" aria-hidden="true">{nf(counts[k])}</span>
              </button>
            ))}
          </div>
          <span className="ds-count rt-showing"><span aria-live="polite">Showing {nf(shown.length)} of {nf(events.length)}</span> <span aria-hidden="true">·</span> Times in UTC</span>
        </div>
      )}
      {!events.length && (
        <div className="ds-card ds-empty" data-testid="timeline-empty">
          <h2 className="ds-empty-title">No events recorded for this run</h2>
          <p className="ds-helper">Events appear here once the run's traces have been ingested.</p>
          {agentId && <Link className="ds-btn ds-btn-ghost" to={agentPath(agentId)}>Back to {agentName}</Link>}
        </div>
      )}
      {events.length > 0 && !shown.length && (
        <div className="ds-card ds-empty">
          <h2 className="ds-empty-title">No {FILTER_NAME[filter]} events in this run</h2>
          {clearFilter}
        </div>
      )}
      {shown.length > 0 && (
        <ol className="rt-list ds-card">
          {shown.map(({ ev, i, v }) => {
            const body = ev.kind === 'tool_call' ? payloadText(ev.args_redacted)
              : (ev.kind === 'input' || ev.kind === 'output') ? clean(fmtJson(content(ev))) : ''
            return (
              <li key={`${ev.span_id || ev.kind}-${i}`} tabIndex={-1}
                  className={`rt-ev rt-${ev.kind}${v ? ' rt-violation' : ''}${flash && flash === ev.span_id ? ' rt-flash' : ''}`}
                  data-testid="timeline-event" data-kind={ev.kind} data-span-id={ev.span_id ?? ''}
                  data-violation={v ? 'true' : 'false'}
                  data-violation-reason={v ? clean(v.reason || 'tool_not_in_manifest') : undefined}>
                <time className="rt-time" data-testid="timeline-event-time" dateTime={ev.ts} title={fmtTs(ev.ts)}>{isoTime(ev.ts) || '—'}</time>
                <span className="rt-rail"><span className="rt-node"><Icon kind={ev.kind} violation={!!v} /></span></span>
                <div className="rt-body">
                  <div className="rt-line">
                    <span className="ds-chip-tag rt-type" data-testid="timeline-event-type">{kindLabel(ev.kind)}</span>
                    {ev.kind === 'model_call'
                      ? <span className="rt-sub">{label(ev)}</span>
                      : <strong className="rt-name">{label(ev)}</strong>}
                    {v && <span className="badge violation-badge rt-badge">Violation: {REASONS[v.reason] || 'Outside manifest'}</span>}
                  </div>
                  {v && v.reason === 'value_limit_exceeded' && (
                    <div className="rt-note">Observed {num(v.observed_value) ?? '—'}, limit {num(v.limit) ?? '—'}</div>
                  )}
                  {v && v.note && <div className="rt-note">{clean(v.note)}</div>}
                  {v && agentId && v.findingId != null && findingIds.has(v.findingId) && (
                    <Link className="rt-finding-link" to={`${agentPath(agentId)}#finding-${v.findingId}`}>View finding<Arrow /></Link>
                  )}
                  {body && <Payload full={body} testid={ev.kind === 'tool_call' ? 'timeline-event-args' : 'timeline-event-content'} what={ev.kind === 'tool_call' ? 'arguments' : 'content'} />}
                  {(ev.kind === 'approval' || ev.kind === 'override') && (
                    <div className="rt-note rt-note-plain">{clean(ev.actor)}{ev.decision ? ` · ${clean(ev.decision)}` : ''}{ev.reason ? ` · ${clean(ev.reason)}` : ''}</div>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
