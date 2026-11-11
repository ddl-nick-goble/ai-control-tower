import React from 'react'
import { settingsApi, refreshHealth } from '../api'
import { PageHeader, Button } from '../components/form'
import '../settings.css'

const FIELD_COPY = {
  llm_provider: ['LLM provider', 'Type gateway to call the LLM Gateway, or mock for the offline stand-in. Empty means gateway.', 'gateway'],
  llm_gateway_url: ['LLM Gateway URL', 'The address of the LLM Gateway app that answers model calls.', 'https://'],
  break_classifier_endpoint_url: ['Break-classifier endpoint URL', 'The model endpoint that classifies reconciliation breaks.', 'https://'],
  break_classifier_access_key: ['Break-classifier access key', 'Used to call the break-classifier endpoint.'],
  external_allow_hosts: ['External card allowed hosts', 'Comma-separated hosts that external agent cards may be fetched from.', 'cards.example.com'],
  trusted_jwks_path: ['Trusted JWKS', 'Path to the JSON Web Key Set that signed external cards must verify against.', '/path/to/jwks.json'],
  service_accounts: ['Service accounts', 'Comma-separated Domino service accounts that count as a distinct agent identity.', 'svc-agent-one, svc-agent-two'],
  policy_agentic_id: ['Governance policy id (agentic bundle)', 'The Governance policy used for agentic system bundles.'],
  policy_model_id: ['Governance policy id (model bundle)', 'The Governance policy used for model bundles.'],
  admins: ['Admins', 'Comma-separated Domino usernames who may view and change Settings. The project owner always can.', 'ann, carl'],
}
const BLOCKS = [
  ['AWS AgentCore', [['aws_role_arn', 'AWS role ARN'], ['aws_external_id', 'AWS external ID'],
    ['aws_access_key_id', 'AWS access key ID'], ['aws_secret_access_key', 'AWS secret access key']]],
  ['Google Agent Platform', [['google_service_account_json', 'Google service-account JSON']]],
  ['Microsoft Agent 365', [['microsoft_tenant_id', 'Microsoft tenant ID'], ['microsoft_client_id', 'Microsoft client ID'],
    ['microsoft_client_secret', 'Microsoft client secret']]],
  ['Databricks', [['databricks_host', 'Databricks host'], ['databricks_client_id', 'Databricks client ID'],
    ['databricks_client_secret', 'Databricks client secret']]],
]

function when(iso) {
  const d = new Date(iso)
  return isNaN(d) ? '' : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function problem(err) {
  if (!err) return 'The change could not be saved. Try again.'
  if (err.status === 423) return 'This value is set by a project variable and cannot be changed here.'
  if (err.status === 403 || err.status === 401) return 'You do not have permission to change settings.'
  if (err.status === 422) return err.message || 'That value is not valid.'
  if (err.status === 409) return err.message || 'That change was refused.'
  return 'The change could not be saved. Try again.'
}

function SettingField({ entry, label, help, placeholder, onChange }) {
  const id = React.useId()
  const helpId = `${id}-h`
  const secret = entry.secret
  const [draft, setDraft] = React.useState(secret ? '' : entry.value || '')
  const [replacing, setReplacing] = React.useState(false)
  const [st, setSt] = React.useState({ kind: 'idle', msg: '' })
  const saved = entry.value || ''
  React.useEffect(() => { if (!secret) setDraft(entry.value || '') }, [entry.value, secret])

  const run = async (fn, okKind) => {
    setSt({ kind: 'busy', msg: '' })
    try {
      const next = await fn()
      onChange(next)
      setDraft(secret ? '' : next.value || '')
      setReplacing(false)
      setSt({ kind: okKind, msg: '' })
    } catch (e) { setSt({ kind: 'error', msg: problem(e) }) }
  }
  const save = () => run(() => settingsApi.put(entry.name, draft), 'saved')
  const clear = () => {
    if (!window.confirm(`Clear ${label}? Its stored value will be removed.`)) return
    run(() => settingsApi.del(entry.name), 'cleared')
  }
  const busy = st.kind === 'busy'
  const showInput = !entry.locked && (!secret || !entry.is_set || replacing)
  const canSave = !busy && draft.trim() !== '' && (secret || draft !== saved)

  let state = null
  if (entry.locked) state = <span className="st-state st-state-lock">Set by project variable</span>
  else if (secret && entry.is_set) state = <span className="st-state st-state-set">Set · changed by {entry.changed_by} on {when(entry.changed_at)}</span>
  else if (secret) state = <span className="st-state">Not set</span>
  else if (entry.source === 'app' && entry.changed_by) state = <span className="st-state">Changed by {entry.changed_by} on {when(entry.changed_at)}</span>

  return (
    <fieldset className="st-field" data-testid={`setting-${entry.name}`}>
      <legend className="st-legend">{label}</legend>
      <label htmlFor={id} className="ds-sr">{label}</label>
      <div className="st-row">
        {entry.locked && !secret && (
          <input id={id} className="ds-input" type="text" readOnly value={entry.value || ''} aria-describedby={helpId} />
        )}
        {showInput && (
          <input id={id} className="ds-input" type={secret ? 'password' : 'text'} value={draft} placeholder={placeholder}
                 autoComplete={secret ? 'new-password' : 'off'} spellCheck="false" aria-describedby={helpId}
                 onChange={(e) => { setDraft(e.target.value); if (st.kind !== 'idle') setSt({ kind: 'idle', msg: '' }) }}
                 onKeyDown={(e) => { if (e.key === 'Enter' && canSave) { e.preventDefault(); save() } }} />
        )}
        {!showInput && !(entry.locked && !secret) && <div className="st-secret-line">{state}</div>}
        {!entry.locked && showInput && <Button variant="primary" onClick={save} disabled={!canSave} busy={busy}>Save</Button>}
        {!entry.locked && secret && entry.is_set && !replacing && <Button onClick={() => { setReplacing(true); setSt({ kind: 'idle', msg: '' }) }} disabled={busy}>Replace</Button>}
        {!entry.locked && secret && replacing && <Button onClick={() => { setReplacing(false); setDraft('') }} disabled={busy}>Cancel</Button>}
        {!entry.locked && entry.source === 'app' && !replacing && <Button onClick={clear} disabled={busy}>Clear</Button>}
      </div>
      {(showInput || (entry.locked && !secret)) && state && <div className="st-meta">{state}</div>}
      <p id={helpId} className="ds-helper st-help">{help}</p>
      <p className="st-status" role="status">{st.kind === 'saved' ? 'Saved' : st.kind === 'cleared' ? 'Cleared' : ''}</p>
      {st.kind === 'error' && <p className="st-error" role="alert">{st.msg}</p>}
    </fieldset>
  )
}

function ConfirmDialog({ title, body, confirm, onConfirm, onCancel }) {
  const ref = React.useRef(null)
  React.useEffect(() => {
    const prev = document.activeElement
    ref.current?.querySelector('[data-autofocus]')?.focus()
    return () => { if (prev && prev.focus) prev.focus() }
  }, [])
  const onKey = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); onCancel() }
    if (e.key === 'Tab') {
      const els = [...ref.current.querySelectorAll('button')]
      const first = els[0], last = els[els.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
  }
  return (
    <div className="st-scrim" onKeyDown={onKey}>
      <div className="st-dialog ds-card" role="dialog" aria-modal="true" aria-labelledby="st-dlg-title" aria-describedby="st-dlg-body" ref={ref}>
        <h2 id="st-dlg-title" className="ds-section-title">{title}</h2>
        <p id="st-dlg-body" className="ds-helper st-dlg-body">{body}</p>
        <div className="st-dlg-actions">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="primary" onClick={onConfirm} data-autofocus>Confirm</Button>
        </div>
      </div>
    </div>
  )
}

function DemoSwitch({ entry, onChange }) {
  const [ask, setAsk] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')
  const on = !!entry.value
  const flip = async () => {
    setAsk(false); setBusy(true); setErr('')
    try {
      const next = await settingsApi.put('demo_mode', !on)
      onChange(next)
      refreshHealth()
    } catch (e) { setErr(problem(e)) } finally { setBusy(false) }
  }
  const labelId = 'st-demo-label'
  return (
    <div className="st-demo">
      <div className="st-demo-row">
        <button type="button" role="switch" aria-checked={on} aria-labelledby={labelId} aria-describedby="st-demo-help"
                className="st-switch" disabled={entry.locked || busy} onClick={() => setAsk(true)}><span className="st-knob" /></button>
        <div>
          <span id={labelId} className="ds-label">Demo mode</span>
          <p id="st-demo-help" className="ds-helper st-help">
            {entry.locked ? 'Set by project variable. Change the variable to turn demo mode on or off.'
              : 'Shows simulated data from a separate demo database. Your real data is never changed.'}
          </p>
        </div>
      </div>
      {busy && <p className="st-status" role="status">{on ? 'Switching to your real data…' : 'Switching to demo data. Seeding can take a few seconds…'}</p>}
      {err && <p className="st-error" role="alert">{err}</p>}
      {ask && (
        <ConfirmDialog
          title={on ? 'Turn demo mode off?' : 'Turn demo mode on?'}
          body={on ? 'The app goes back to your real database. Demo data stays in its own database.'
            : 'The app switches to a separate demo database with simulated agents, runs and a simulated approver. Your real database is not touched.'}
          confirm="Confirm" onConfirm={flip} onCancel={() => setAsk(false)} />
      )}
    </div>
  )
}

function Card({ title, helper, children }) {
  const id = React.useId()
  return (
    <section className="ds-card st-section" aria-labelledby={id}>
      <header className="st-section-head">
        <h2 id={id} className="ds-section-title">{title}</h2>
        {helper && <p className="ds-helper">{helper}</p>}
      </header>
      <div className="st-section-body">{children}</div>
    </section>
  )
}

export default function Settings() {
  const [state, setState] = React.useState({ phase: 'loading' })
  const load = React.useCallback(() => {
    setState({ phase: 'loading' })
    settingsApi.list().then(
      (data) => setState({ phase: 'ready', data }),
      (e) => setState(e.status === 401 || e.status === 403 ? { phase: 'denied' } : { phase: 'error' }))
  }, [])
  React.useEffect(load, [load])

  const update = (next) => setState((s) => ({
    ...s, data: { ...s.data, settings: s.data.settings.map((x) => (x.name === next.name ? next : x)) },
  }))

  let body
  if (state.phase === 'loading') {
    body = (
      <div role="status" data-testid="settings-loading" className="st-loading">
        <span className="ds-sr">Loading settings</span>
        <span className="ds-skel" aria-hidden="true" style={{ width: '40%' }} />
        <span className="ds-skel" aria-hidden="true" style={{ width: '90%' }} />
        <span className="ds-skel" aria-hidden="true" style={{ width: '70%' }} />
      </div>
    )
  } else if (state.phase === 'denied') {
    body = (
      <div className="ds-card ds-empty" data-testid="settings-no-access">
        <h2 className="ds-empty-title">You have no access to Settings</h2>
        <p className="ds-helper">Settings are limited to the project owner and the admins they list. Ask the project owner if you need a change.</p>
      </div>
    )
  } else if (state.phase === 'error') {
    body = (
      <div className="ds-card ds-empty ds-empty-error" data-testid="settings-error" role="alert">
        <h2 className="ds-empty-title">We could not load Settings</h2>
        <p className="ds-helper">Something went wrong on our side. Your settings are unchanged.</p>
        <Button variant="primary" onClick={load}>Try again</Button>
      </div>
    )
  } else {
    const by = Object.fromEntries(state.data.settings.map((s) => [s.name, s]))
    const field = (name, label, help, ph) => <SettingField key={name} entry={by[name]} label={label} help={help} placeholder={ph} onChange={update} />
    const copy = (name) => field(name, ...FIELD_COPY[name].slice(0, 1), FIELD_COPY[name][1], FIELD_COPY[name][2])
    body = (
      <div className="st-stack" data-testid="settings-body">
        {state.data.warnings.length > 0 && (
          <div className="st-warning" role="status" data-testid="secret-key-warning">
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5l8 14H2z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M10 8v4M10 14.2v.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
            <div>{state.data.warnings.map((w) => <p key={w}>{w}</p>)}</div>
          </div>
        )}
        <Card title="Demo mode" helper="Try the app with simulated data, without touching your real database.">
          <DemoSwitch entry={by.demo_mode} onChange={update} />
        </Card>
        <Card title="Platform credentials" helper="Secrets are write-only: once saved they are never shown again.">
          {BLOCKS.map(([title, items]) => (
            <fieldset className="st-block" key={title}>
              <legend className="st-block-title">{title}</legend>
              {items.map(([name, label]) => field(name, label, by[name].secret ? 'Stored encrypted. Replace or clear it later.' : '', null))}
            </fieldset>
          ))}
        </Card>
        <Card title="Integrations" helper="A value set by a project variable wins and is read-only here.">
          {['llm_provider', 'llm_gateway_url', 'break_classifier_endpoint_url', 'break_classifier_access_key', 'external_allow_hosts',
            'trusted_jwks_path', 'service_accounts', 'policy_agentic_id', 'policy_model_id'].map(copy)}
        </Card>
        <Card title="Access" helper="Only admins can open this page.">{copy('admins')}</Card>
      </div>
    )
  }
  return (
    <div className="ds-page ds-container" data-testid="settings-page">
      <PageHeader title="Settings" description="Connections, credentials and access for this Control Tower. Changes apply within seconds, with no restart." />
      {body}
    </div>
  )
}
