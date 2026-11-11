import React from 'react'
import { createPortal } from 'react-dom'
import { settingsApi, refreshHealth } from '../api'
import { PageHeader, Button } from '../components/form'
import '../settings.css'

// label, one-line hint, placeholder (a format example only, never a plausible value)
const COPY = {
  llm_provider: ['LLM provider', 'Default: gateway. Use mock for the offline stand-in.'],
  llm_gateway_url: ['LLM Gateway URL', 'The LLM Gateway app that answers model calls.', 'https://'],
  break_classifier_endpoint_url: ['Break-classifier endpoint URL', 'The model endpoint that classifies reconciliation breaks.', 'https://'],
  break_classifier_access_key: ['Break-classifier access key', 'Used to call the break-classifier endpoint.'],
  external_allow_hosts: ['External card allowed hosts', 'Comma-separated hosts external agent cards may be fetched from.'],
  trusted_jwks_path: ['Trusted JWKS', 'File path of the key set that signed external cards must verify against.'],
  service_accounts: ['Service accounts', 'Comma-separated Domino service accounts that count as a distinct agent identity.'],
  policy_agentic_id: ['Governance policy id (agentic bundle)', 'The Governance policy used for agentic system bundles.'],
  policy_model_id: ['Governance policy id (model bundle)', 'The Governance policy used for model bundles.'],
  admins: ['Admins', 'Comma-separated Domino usernames. The project owner always has access.'],
}
const PLATFORMS = [
  { id: 'aws', title: 'AWS AgentCore', blurb: 'An IAM role that ACT assumes (with an external ID), or an access key as a fallback.',
    fields: [['aws_role_arn', 'AWS role ARN'], ['aws_external_id', 'AWS external ID'],
      ['aws_access_key_id', 'AWS access key ID'], ['aws_secret_access_key', 'AWS secret access key']],
    ready: (v) => v.aws_role_arn || (v.aws_access_key_id && v.aws_secret_access_key) },
  { id: 'google', title: 'Google Agent Platform', blurb: 'A service-account key as JSON.',
    fields: [['google_service_account_json', 'Google service-account JSON']],
    ready: (v) => v.google_service_account_json },
  { id: 'microsoft', title: 'Microsoft Agent 365', blurb: 'An Entra app registration, client-credentials flow.',
    fields: [['microsoft_tenant_id', 'Microsoft tenant ID'], ['microsoft_client_id', 'Microsoft client ID'],
      ['microsoft_client_secret', 'Microsoft client secret']],
    ready: (v) => v.microsoft_tenant_id && v.microsoft_client_id && v.microsoft_client_secret },
  { id: 'databricks', title: 'Databricks', blurb: 'A workspace URL and a service principal (OAuth M2M).',
    fields: [['databricks_host', 'Databricks host'], ['databricks_client_id', 'Databricks client ID'],
      ['databricks_client_secret', 'Databricks client secret']],
    ready: (v) => v.databricks_host && v.databricks_client_id && v.databricks_client_secret },
]
const NAV = [
  ['st-demo', 'Demo mode'],
  ['st-platforms', 'Platform credentials', [['st-aws', 'AWS AgentCore'], ['st-google', 'Google Agent Platform'], ['st-microsoft', 'Microsoft Agent 365'], ['st-databricks', 'Databricks']]],
  ['st-integrations', 'Integrations'],
  ['st-access', 'Access'],
]

const cx = (...a) => a.filter(Boolean).join(' ')

function when(iso) {
  const d = new Date(iso)
  return isNaN(d) ? '' : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}
const sentence = (t) => { t = (t || '').trim(); return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? '' : '.') : t }

function problem(err) {
  if (!err) return 'The change could not be saved. Try again.'
  if (err.status === 423) return 'This value is set by a project variable and cannot be changed here.'
  if (err.status === 403 || err.status === 401) return 'You do not have permission to change settings.'
  if (err.status === 422) return sentence(err.message) || 'That value is not valid.'
  if (err.status === 409) return sentence(err.message) || 'That change was refused.'
  return 'The change could not be saved. Try again.'
}

const Icon = {
  lock: <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>,
  warn: <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5l8 14H2z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M10 8v4M10 14.2v.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>,
  err: <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M10 5.5v5.5M10 13.5v1.2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>,
  ok: <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10.5l4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>,
}

function Badge({ tone = 'none', children }) {
  return <span className={`st-badge st-badge-${tone}`}>{tone === 'lock' && Icon.lock}{children}</span>
}

/** One setting. layout "row": settings-table row (label+hint | control | status); "stack": compact, for platform cards. */
function SettingField({ entry, label, hint, placeholder, layout = 'row', onChange }) {
  const id = React.useId()
  const secret = entry.secret
  const saved = entry.value || ''
  const [draft, setDraft] = React.useState(secret ? '' : saved)
  const [replacing, setReplacing] = React.useState(false)
  const [ask, setAsk] = React.useState(false)
  const [st, setSt] = React.useState({ kind: 'idle', msg: '' })
  const timer = React.useRef(null)
  React.useEffect(() => { if (!secret) setDraft(entry.value || '') }, [entry.value, secret])
  React.useEffect(() => () => clearTimeout(timer.current), [])

  const unreadable = secret && entry.source === 'app' && entry.decryptable === false
  const hasValue = secret && entry.is_set && !unreadable
  const showInput = !entry.locked && (!secret || !hasValue || replacing)
  const dirty = secret ? draft !== '' : draft !== saved
  const canSave = dirty && draft.trim() !== '' && st.kind !== 'busy'
  const busy = st.kind === 'busy'
  const invalid = st.kind === 'error'

  const done = (kind) => {
    setSt({ kind, msg: '' })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setSt((s) => (s.kind === kind ? { kind: 'idle', msg: '' } : s)), 4000)
  }
  const run = async (fn, okKind) => {
    setSt({ kind: 'busy', msg: '' })
    try {
      const next = await fn()
      onChange(next)
      setDraft(secret ? '' : next.value || '')
      setReplacing(false)
      done(okKind)
    } catch (e) { setSt({ kind: 'error', msg: problem(e) }) }
  }
  const save = () => { if (canSave) run(() => settingsApi.put(entry.name, draft), 'saved') }
  const clear = () => { setAsk(false); run(() => settingsApi.del(entry.name), 'cleared') }
  const edit = (v) => { setDraft(v); if (st.kind === 'error') setSt({ kind: 'idle', msg: '' }) }

  let badge
  if (entry.locked) badge = <Badge tone="lock">Set by project variable</Badge>
  else if (unreadable) badge = <Badge tone="warn">Re-enter</Badge>
  else if (secret && entry.is_set) badge = <><Badge tone="ok">Set</Badge><span className="st-by"> · changed by {entry.changed_by} on {when(entry.changed_at)}</span></>
  else if (secret) badge = <Badge>Not set</Badge>
  else if (entry.source === 'app') badge = <><Badge tone="ok">Set</Badge><span className="st-by"> · changed by {entry.changed_by} on {when(entry.changed_at)}</span></>
  else badge = <Badge>Not set</Badge>

  let hintText = hint
  if (entry.locked) hintText = <>Set by the project variable <code>{entry.env}</code>. Change it there.</>
  else if (unreadable) hintText = 'The secret key changed, so this value can no longer be read. Enter it again.'

  const labelId = `${id}-l`, hintId = `${id}-h`
  const plainLocked = entry.locked
  return (
    <div role="group" aria-labelledby={labelId} className={cx('st-f', `st-f-${layout}`, invalid && 'st-f-invalid')} data-testid={`setting-${entry.name}`}>
      {showInput || plainLocked && !secret
        ? <label id={labelId} htmlFor={`${id}-i`} className="st-label">{label}</label>
        : <span id={labelId} className="st-label">{label}</span>}
      <div className="st-state">{badge}</div>
      <div className="st-ctl">
        {plainLocked && !secret && <input id={`${id}-i`} className="ds-input" type="text" readOnly value={entry.value || ''} aria-describedby={hintId} />}
        {plainLocked && secret && <div className="st-masked st-masked-lock" aria-hidden="true">••••••••</div>}
        {showInput && (
          <input id={`${id}-i`} className="ds-input" type={secret ? 'password' : 'text'} value={draft} placeholder={placeholder}
                 autoComplete={secret ? 'new-password' : 'off'} spellCheck="false" aria-describedby={hintId}
                 aria-invalid={invalid ? 'true' : undefined}
                 onChange={(e) => edit(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); save() } }} />
        )}
        {!showInput && !plainLocked && <div className="st-masked" aria-hidden="true">••••••••</div>}
        {showInput && dirty && <Button variant="primary" onClick={save} disabled={!canSave} busy={busy}>Save</Button>}
        {replacing && !busy && <Button onClick={() => { setReplacing(false); setDraft(''); setSt({ kind: 'idle', msg: '' }) }}>Cancel</Button>}
        {!entry.locked && hasValue && !replacing && <Button onClick={() => { setReplacing(true); setSt({ kind: 'idle', msg: '' }) }} disabled={busy}>Replace</Button>}
        {!entry.locked && entry.source === 'app' && !replacing && !(showInput && dirty) && (
          <Button variant="ghost" className="st-danger" onClick={() => setAsk(true)} disabled={busy}>Clear</Button>)}
        <span className="st-saved" role="status">{st.kind === 'saved' ? <>{Icon.ok}Saved</> : st.kind === 'cleared' ? <>{Icon.ok}Cleared</> : ''}</span>
      </div>
      {invalid
        ? <p id={hintId} className="st-hint st-hint-error" role="alert">{Icon.err}<span>{st.msg}</span></p>
        : <p id={hintId} className="st-hint">{hintText}</p>}
      {ask && (
        <ConfirmDialog title={`Clear ${label}?`} danger onConfirm={clear} onCancel={() => setAsk(false)}
                       body={secret ? 'The stored secret is deleted and cannot be recovered. You can enter a new one later.'
                         : 'The saved value is removed and the default applies.'} />
      )}
    </div>
  )
}

function ConfirmDialog({ title, body, danger, onConfirm, onCancel }) {
  const ref = React.useRef(null)
  const tid = React.useId(), bid = React.useId()
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
  return createPortal(
    <div className="st-scrim" onKeyDown={onKey} onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="st-dialog ds-card" role="dialog" aria-modal="true" aria-labelledby={tid} aria-describedby={bid} ref={ref}>
        <h2 id={tid} className="ds-section-title">{title}</h2>
        <p id={bid} className="st-dlg-body">{body}</p>
        <div className="st-dlg-actions">
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="primary" className={danger ? 'st-btn-danger' : undefined} onClick={onConfirm} data-autofocus>Confirm</Button>
        </div>
      </div>
    </div>, document.body)
}

function DemoRow({ entry, onChange }) {
  const [ask, setAsk] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')
  const on = !!entry.value
  const flip = async () => {
    setAsk(false); setBusy(true); setErr('')
    try {
      onChange(await settingsApi.put('demo_mode', !on))
      refreshHealth()
    } catch (e) { setErr(problem(e)) } finally { setBusy(false) }
  }
  return (
    <div className="st-f st-f-row st-demo" data-testid="setting-demo_mode">
      <span id="st-demo-label" className="st-label">Demo mode</span>
      <div className="st-state">{entry.locked ? <Badge tone="lock">Set by project variable</Badge> : <Badge tone={on ? 'ok' : 'none'}>{on ? 'On' : 'Off'}</Badge>}</div>
      <div className="st-ctl">
        <button type="button" role="switch" aria-checked={on} aria-labelledby="st-demo-label" aria-describedby="st-demo-help"
                className="st-switch" disabled={entry.locked || busy} onClick={() => setAsk(true)}><span className="st-knob" /></button>
        <span className="st-switch-text" aria-hidden="true">{busy ? (on ? 'Switching to your real data…' : 'Switching to demo data…') : on ? 'On' : 'Off'}</span>
        <span className="ds-sr" role="status">{busy ? (on ? 'Switching to your real data' : 'Switching to demo data. Seeding can take a few seconds') : ''}</span>
      </div>
      {err
        ? <p id="st-demo-help" className="st-hint st-hint-error" role="alert">{Icon.err}<span>{err}</span></p>
        : <p id="st-demo-help" className="st-hint">
          {entry.locked ? <>Set by the project variable <code>ACT_DEMO_MODE</code>. Change it there.</>
            : 'Shows simulated data from a separate demo database. Your real data is never changed.'}</p>}
      {ask && (
        <ConfirmDialog
          title={on ? 'Turn demo mode off?' : 'Turn demo mode on?'}
          body={on ? 'The app goes back to your real database. Demo data stays in its own database.'
            : 'The app switches to a separate demo database with simulated agents, runs and a simulated approver. Your real database is not touched.'}
          onConfirm={flip} onCancel={() => setAsk(false)} />
      )}
    </div>
  )
}

function Card({ id, title, subtitle, children }) {
  const hid = `${id}-h`
  return (
    <section id={id} className="ds-card st-card" aria-labelledby={hid}>
      <header className="st-card-head">
        <h2 id={hid} className="ds-section-title">{title}</h2>
        {subtitle && <p className="st-sub">{subtitle}</p>}
      </header>
      <div className="st-rows">{children}</div>
    </section>
  )
}

function PlatformCard({ p, by, update }) {
  const vals = Object.fromEntries(p.fields.map(([n]) => [n, by[n].is_set]))
  const any = Object.values(vals).some(Boolean)
  const ready = !!p.ready(vals)
  const tone = ready ? 'ok' : any ? 'warn' : 'none'
  const hid = `st-${p.id}-h`
  return (
    <section id={`st-${p.id}`} role="group" aria-labelledby={hid} className="ds-card st-plat">
      <header className="st-plat-head">
        <h3 id={hid} className="st-plat-title">{p.title}</h3>
        <Badge tone={tone}>{ready ? 'Configured' : any ? 'Incomplete' : 'Not configured'}</Badge>
      </header>
      <p className="st-sub st-plat-blurb">{p.blurb}</p>
      <div className="st-plat-fields">
        {p.fields.map(([n, label]) => <SettingField key={n} layout="stack" entry={by[n]} label={label} onChange={update} />)}
      </div>
    </section>
  )
}

function SectionNav({ ready }) {
  const [active, setActive] = React.useState('st-demo')
  React.useEffect(() => {
    if (!ready || !('IntersectionObserver' in window)) return undefined
    const ids = NAV.flatMap(([id, , subs]) => [id, ...(subs || []).map((s) => s[0])])
    const seen = new Map()
    const io = new IntersectionObserver((es) => {
      es.forEach((e) => seen.set(e.target.id, e.isIntersecting))
      const top = ids.find((i) => seen.get(i))
      if (top) setActive(top)
    }, { rootMargin: '-10% 0px -70% 0px' })
    ids.forEach((i) => { const el = document.getElementById(i); if (el) io.observe(el) })
    return () => io.disconnect()
  }, [ready])
  const go = (id) => (e) => {
    e.preventDefault()
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    setActive(id)
  }
  const link = (id, text, sub) => (
    <a key={id} href={`#/settings`} onClick={go(id)} className={cx('st-nav-link', sub && 'st-nav-sub')} aria-current={active === id ? 'location' : undefined}>{text}</a>
  )
  return (
    <nav className="st-nav" aria-label="Settings sections">
      {NAV.map(([id, text, subs]) => (
        <React.Fragment key={id}>
          {link(id, text)}
          {subs && subs.map(([sid, st]) => link(sid, st, true))}
        </React.Fragment>
      ))}
    </nav>
  )
}

function Skeleton() {
  const row = (k) => (
    <div className="st-sk-row" key={k}>
      <div><span className="ds-skel" style={{ width: '60%' }} /><span className="ds-skel ds-skel-s" style={{ width: '85%' }} /></div>
      <span className="ds-skel st-sk-input" />
    </div>
  )
  const card = (k, n) => (
    <div className="ds-card st-card" key={k}>
      <div className="st-card-head"><span className="ds-skel" style={{ width: 180, height: 18 }} /></div>
      <div className="st-rows">{Array.from({ length: n }, (_, i) => row(i))}</div>
    </div>
  )
  return (
    <div role="status" data-testid="settings-loading" className="st-layout st-loading" aria-busy="true">
      <span className="ds-sr">Loading settings</span>
      <div className="st-nav st-nav-sk" aria-hidden="true">{[0, 1, 2, 3].map((i) => <span key={i} className="ds-skel" style={{ width: '70%' }} />)}</div>
      <div className="st-main" aria-hidden="true">{card('a', 1)}{card('b', 3)}{card('c', 3)}</div>
    </div>
  )
}

export default function Settings() {
  const [state, setState] = React.useState({ phase: 'loading' })
  const load = React.useCallback(() => {
    setState({ phase: 'loading' })
    settingsApi.list().then(
      (data) => setState({ phase: 'ready', data }),
      (e) => {
        if (e.status === 401 || e.status === 403) {
          settingsApi.access().then((a) => setState({ phase: 'denied', unconfigured: a && a.admins_configured === false }),
            () => setState({ phase: 'denied' }))
        } else setState({ phase: 'error' })
      })
  }, [])
  React.useEffect(load, [load])

  const update = (next) => setState((s) => ({
    ...s, data: { ...s.data, settings: s.data.settings.map((x) => (x.name === next.name ? next : x)) },
  }))

  let body
  if (state.phase === 'loading') {
    body = <Skeleton />
  } else if (state.phase === 'denied') {
    body = (
      <div className="ds-card ds-empty" data-testid="settings-no-access">
        <h2 className="ds-empty-title">You have no access to Settings</h2>
        <p className="ds-helper">
          {state.unconfigured
            ? 'No project owner or admins are configured, so nobody can open Settings yet. Set the project variable ACT_ADMINS to a list of usernames.'
            : 'Settings are limited to the project owner and the admins they list. Ask the project owner if you need a change.'}
        </p>
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
    const row = (name) => <SettingField key={name} entry={by[name]} label={COPY[name][0]} hint={COPY[name][1]} placeholder={COPY[name][2]} onChange={update} />
    body = (
      <>
        {state.data.warnings.length > 0 && (
          <div className="st-warning" role="status" data-testid="secret-key-warning">
            {Icon.warn}
            <div>{state.data.warnings.map((w) => <p key={w}>{w.split(/(ACT_SECRET_KEY)/).map((t, i) => (t === 'ACT_SECRET_KEY' ? <code key={i}>{t}</code> : t))}</p>)}</div>
          </div>
        )}
        <div className="st-layout" data-testid="settings-body">
          <SectionNav ready />
          <div className="st-main">
            <Card id="st-demo" title="Demo mode"><DemoRow entry={by.demo_mode} onChange={update} /></Card>
            <section id="st-platforms" className="st-platforms" aria-labelledby="st-platforms-h">
              <header className="st-plat-section-head">
                <h2 id="st-platforms-h" className="ds-section-title">Platform credentials</h2>
                <p className="st-sub">Used by the import wizard to read agents from each platform. Secrets are encrypted, write-only and never shown again.</p>
              </header>
              <div className="st-plat-grid">
                {PLATFORMS.map((p) => <PlatformCard key={p.id} p={p} by={by} update={update} />)}
              </div>
            </section>
            <Card id="st-integrations" title="Integrations" subtitle="A project variable with the same name wins and is read-only here. Secrets are encrypted and write-only.">
              {['llm_provider', 'llm_gateway_url', 'break_classifier_endpoint_url', 'break_classifier_access_key', 'external_allow_hosts',
                'trusted_jwks_path', 'service_accounts', 'policy_agentic_id', 'policy_model_id'].map(row)}
            </Card>
            <Card id="st-access" title="Access" subtitle="Only the project owner and these admins can open this page.">{row('admins')}</Card>
          </div>
        </div>
      </>
    )
  }
  return (
    <div className="ds-page ds-container" data-testid="settings-page">
      <PageHeader title="Settings" description="Connections, credentials and access for this Control Tower. Changes apply within seconds, with no restart." />
      {body}
    </div>
  )
}
