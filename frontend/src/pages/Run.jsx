import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { clean, cleanText, fmtMessages, fmtTs } from '../format'
import { ErrorBox, Loading, useAsync } from '../components/ui'

// Never show provider/model ids from trace data.
function label(ev) {
  if (ev.kind === 'model_call') {
    const a = ev.attrs || {}
    const mock = String(a['act.llm.provider'] || '').toLowerCase() === 'mock'
      || /mock-llm/i.test(`${ev.name || ''} ${a['gen_ai.request.model'] || ''}`)
    return mock ? 'LLM call · mock LLM (demo, no gateway call)' : 'LLM call · frontier model via gateway'
  }
  return clean(ev.name)
}

const REASONS = {
  tool_not_in_manifest: 'Outside manifest',
  value_limit_exceeded: 'Over limit',
  value_unverifiable: 'Unverifiable value',
}

function content(ev) {
  const a = ev.attrs || {}
  const keys = ev.kind === 'output'
    ? ['gen_ai.output.messages', 'output.value', 'gen_ai.input.messages', 'input.value']
    : ['gen_ai.input.messages', 'input.value', 'gen_ai.output.messages', 'output.value']
  if (ev.content) return fmtMessages(ev.content)
  for (const k of keys) if (a[k]) return fmtMessages(a[k])
  return ''
}

export default function Run() {
  const { runId } = useParams()
  const tl = useAsync(() => api.timeline(runId), [runId])
  const agentId = tl.data?.agent_id
  const fnd = useAsync(() => (agentId ? api.findings(agentId).then((items) => ({ agentId, items })) : Promise.resolve({ agentId: null, items: [] })), [agentId])
  const findingsPending = tl.loading || !tl.data || (!fnd.error && (fnd.loading || fnd.data?.agentId !== (agentId ?? null)))
  const findingsFailed = !!fnd.error

  const violations = React.useMemo(() => {
    const m = new Map()
    for (const f of fnd.data?.items || []) {
      if (f.run_id !== runId || f.status !== 'open') continue
      for (const v of f.detail?.violations || []) if (v.span_id) m.set(v.span_id, { ...v, reason: v.reason || f.reason || 'tool_not_in_manifest' })
      if (f.span_id && !m.has(f.span_id)) m.set(f.span_id, { reason: f.reason || 'tool_not_in_manifest', tool: f.tool, limit: f.limit, observed_value: f.observed_value })
    }
    return m
  }, [fnd.data, runId])

  if (tl.error) return <ErrorBox error={tl.error} />
  if (findingsPending) return <Loading what="timeline" />
  const events = tl.data.events

  return (
    <div data-testid="timeline-page" data-event-count={events.length}>
      <div className="page-head">
        <h1>Run timeline</h1>
        {agentId && <Link to={`/agents/${encodeURIComponent(agentId)}`}>Back to agent</Link>}
      </div>
      <p className="muted">Run {runId} · {fmtTs(tl.data.started_at)} to {fmtTs(tl.data.ended_at)} · {events.length} events</p>
      {findingsFailed && <div className="alert error" role="alert" data-testid="findings-error">Could not load findings, so violations are not highlighted on this timeline.</div>}
      <ol className="timeline">
        {events.map((ev, i) => {
          const v = violations.get(ev.span_id)
          const isV = !!v && ev.kind === 'tool_call'
          return (
            <li key={`${ev.span_id || ev.kind}-${i}`} className={`tl-event kind-${ev.kind}${isV ? ' violation' : ''}`}
                data-testid="timeline-event" data-kind={ev.kind} data-span-id={ev.span_id ?? ''}
                data-violation={isV ? 'true' : 'false'}
                data-violation-reason={isV ? clean(v.reason || 'tool_not_in_manifest') : undefined}>
              <div className="tl-head">
                <span className="tl-ts">{fmtTs(ev.ts)}</span>
                <span className="chip">{ev.kind}</span>
                <strong>{label(ev)}</strong>
                {isV && <span className="badge violation-badge">
                  {REASONS[v.reason] || 'Outside manifest'}
                </span>}
              </div>
              {ev.kind === 'tool_call' && (
                <pre data-testid="timeline-event-args">{cleanText(ev.args_redacted, 2000)}</pre>
              )}
              {isV && v.reason === 'value_limit_exceeded' && (
                <div className="tl-note">Observed {clean(v.observed_value)}, limit {clean(v.limit)}</div>
              )}
              {isV && v.note && <div className="tl-note">{clean(v.note)}</div>}
              {(ev.kind === 'input' || ev.kind === 'output') && content(ev) && (
                <pre className="tl-content" data-testid="timeline-event-content">{cleanText(content(ev), 800)}</pre>
              )}
              {(ev.kind === 'approval' || ev.kind === 'override') && (
                <div className="tl-note">{clean(ev.actor)}{ev.decision ? ` · ${clean(ev.decision)}` : ''}{ev.reason ? ` · ${clean(ev.reason)}` : ''}</div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
