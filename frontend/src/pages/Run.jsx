import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { fmtJson, fmtTs } from '../format'
import { ErrorBox, Loading, useAsync } from '../components/ui'

// Never show provider/model ids from trace data.
function label(ev) {
  if (ev.kind === 'model_call') return 'LLM call · frontier model via gateway'
  return ev.name
}

export default function Run() {
  const { runId } = useParams()
  const tl = useAsync(() => api.timeline(runId), [runId])
  const agentId = tl.data?.agent_id
  const fnd = useAsync(() => (agentId ? api.findings(agentId) : Promise.resolve([])), [agentId])

  const violations = React.useMemo(() => {
    const m = new Map()
    for (const f of fnd.data || []) {
      if (f.run_id !== runId) continue
      for (const v of f.detail?.violations || []) if (v.span_id) m.set(v.span_id, v)
      if (f.span_id && !m.has(f.span_id)) m.set(f.span_id, { reason: f.reason, tool: f.tool })
    }
    return m
  }, [fnd.data, runId])

  if (tl.error) return <ErrorBox error={tl.error} />
  if (tl.loading || fnd.loading) return <Loading what="timeline" />
  const events = tl.data.events

  return (
    <div data-testid="timeline-page" data-event-count={events.length}>
      <div className="page-head">
        <h1>Run timeline</h1>
        {agentId && <Link to={`/agents/${encodeURIComponent(agentId)}`}>Back to agent</Link>}
      </div>
      <p className="muted">Run {runId} · {fmtTs(tl.data.started_at)} to {fmtTs(tl.data.ended_at)} · {events.length} events</p>
      <ol className="timeline">
        {events.map((ev, i) => {
          const v = violations.get(ev.span_id)
          const isV = !!v && ev.kind === 'tool_call'
          return (
            <li key={`${ev.span_id || ev.kind}-${i}`} className={`tl-event kind-${ev.kind}${isV ? ' violation' : ''}`}
                data-testid="timeline-event" data-kind={ev.kind} data-span-id={ev.span_id ?? ''}
                data-violation={isV ? 'true' : 'false'}>
              <div className="tl-head">
                <span className="tl-ts">{fmtTs(ev.ts)}</span>
                <span className="chip">{ev.kind}</span>
                <strong>{label(ev)}</strong>
                {isV && <span className="badge violation-badge">
                  {v.reason === 'value_limit_exceeded' ? 'Over limit' : 'Outside manifest'}
                </span>}
              </div>
              {ev.kind === 'tool_call' && (
                <pre data-testid="timeline-event-args">{fmtJson(ev.args_redacted)}</pre>
              )}
              {isV && v.reason === 'value_limit_exceeded' && (
                <div className="tl-note">Observed {v.observed_value}, limit {v.limit}</div>
              )}
              {(ev.kind === 'approval' || ev.kind === 'override') && (
                <div className="tl-note">{ev.actor}{ev.decision ? ` · ${ev.decision}` : ''}{ev.reason ? ` · ${ev.reason}` : ''}</div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
