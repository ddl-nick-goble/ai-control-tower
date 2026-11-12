import React from 'react'
import { createPortal } from 'react-dom'
import { settingsApi, refreshHealth } from '../api'
import { PageHeader, Button } from '../components/form'
import '../settings.css'

// Labels and help text come from the API (backend/settings.py DEFS). Placeholders are format examples only.
const PLACEHOLDER = {
  llm_provider: 'mock | gateway', llm_gateway_url: 'https://…', break_classifier_endpoint_url: 'https://…',
  trusted_jwks_path: '/path/to/jwks.json', policy_agentic_id: 'policy id', policy_model_id: 'policy id',
  aws_role_arn: 'arn:aws:iam::123456789012:role/name', aws_external_id: 'external id', aws_access_key_id: 'AKIA…',
  microsoft_tenant_id: 'tenant id (GUID)', microsoft_client_id: 'client id (GUID)',
  databricks_host: 'https://…', databricks_client_id: 'client id',
  external_allow_hosts: 'host1, host2', service_accounts: 'user1, user2',
  admins: 'user1, user2',
}
const INTEGRATIONS = ['llm_provider', 'llm_gateway_url', 'break_classifier_endpoint_url', 'break_classifier_access_key', 'external_allow_hosts',
  'trusted_jwks_path', 'service_accounts', 'policy_agentic_id', 'policy_model_id']
const PLATFORMS = [
  { id: 'aws', title: 'AWS AgentCore', blurb: 'An IAM role ACT assumes, or an access key as a fallback.',
    fields: [['aws_role_arn', true], ['aws_external_id'], ['aws_access_key_id'], ['aws_secret_access_key']],
    ready: (v) => v.aws_role_arn || (v.aws_access_key_id && v.aws_secret_access_key) },
  { id: 'google', title: 'Google Agent Platform', blurb: 'A service-account key as JSON.',
    fields: [['google_service_account_json', true]], ready: (v) => v.google_service_account_json },
  { id: 'microsoft', title: 'Microsoft Agent 365', blurb: 'An Entra app registration, client-credentials flow.',
    fields: [['microsoft_tenant_id', true], ['microsoft_client_id'], ['microsoft_client_secret']],
    ready: (v) => v.microsoft_tenant_id && v.microsoft_client_id && v.microsoft_client_secret },
  { id: 'databricks', title: 'Databricks', blurb: 'A workspace URL and a service principal (OAuth M2M).',
    fields: [['databricks_host', true], ['databricks_client_id'], ['databricks_client_secret']],
    ready: (v) => v.databricks_host && v.databricks_client_id && v.databricks_client_secret },
]
const NAV = [['st-demo', 'Demo'], ['st-integrations', 'Integrations'], ['st-credentials', 'Credentials'], ['st-access', 'Access']]

const cx = (...a) => a.filter(Boolean).join(' ')

function when(iso) {
  const d = new Date(iso)
  return isNaN(d) ? '' : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}
const sentence = (t) => { t = (t || '').trim(); return t ? t[0].toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? '' : '.') : t }

function problem(err) {
  if (!err) return 'The change could not be saved. Try again.'
  if (err.status === 423) return 'This value is set by an environment variable and cannot be changed here.'
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
function SettingField({ entry, layout = 'row', span, onChange, children }) {
  const label = entry.label, hint = entry.help, placeholder = PLACEHOLDER[entry.name]
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

  const setLine = !entry.locked && !unreadable && entry.is_set
    ? `Set · changed by ${entry.changed_by} on ${when(entry.changed_at)}` : ''
  let badge = null
  if (entry.locked) badge = <Badge tone="lock">Set by environment variable {entry.env}</Badge>
  else if (unreadable) badge = <Badge tone="warn">Re-enter</Badge>
  else if (entry.is_set && layout === 'row') badge = <Badge tone="ok">Set</Badge>
  else if (!entry.is_set && layout === 'row') badge = <Badge>Not set</Badge>

  // Help text always shows; env notice and the changed-by line add to it, never replace it.
  const hintText = (
    <>
      {hint && <span className="st-help">{hint}</span>}
      {entry.locked && <span className="st-help st-env">Set by environment variable <code>{entry.env}</code>. Change it there.</span>}
      {unreadable && <span className="st-help">The secret key changed, so this value can no longer be read. Enter it again.</span>}
      {setLine && <span className="st-help st-setline">{setLine}</span>}
    </>
  )

  const labelId = `${id}-l`, hintId = `${id}-h`
  const plainLocked = entry.locked
  return (
    <div role="group" aria-labelledby={labelId} className={cx('st-f', `st-f-${layout}`, entry.locked && 'st-f-locked', span && 'st-span', invalid && 'st-f-invalid')} data-testid={`setting-${entry.name}`}>
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
        : <div id={hintId} className="st-hint">{hintText}</div>}
      {children}
      {ask && (
        <ConfirmDialog title={secret ? 'Clear key' : 'Clear value'} danger confirmLabel={secret ? 'Clear key' : 'Clear value'} onConfirm={clear} onCancel={() => setAsk(false)}
                       body={secret ? `Clear ${label}? The stored secret is deleted and cannot be recovered. You can enter a new one later.`
                         : `Clear ${label}? The saved value is removed and the default applies.`} />
      )}
    </div>
  )
}

function ConfirmDialog({ title, body, danger, confirmLabel, onConfirm, onCancel, busy }) {
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
          <Button variant="primary" className={danger ? 'st-btn-danger' : undefined} onClick={onConfirm} disabled={busy} data-autofocus>
            {confirmLabel || 'Confirm'}</Button>
        </div>
      </div>
    </div>, document.body)
}

function DemoCard({ entry, onChange }) {
  const [ask, setAsk] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')
  const on = !!entry.value
  const [realN, setRealN] = React.useState(null)
  const token = React.useRef(0)
  const openAsk = () => {
    setAsk(true)
    if (on) {
      const mine = ++token.current
      setRealN(null)
      settingsApi.realAgentCount()
        .then((r) => { if (token.current === mine) setRealN(r.real_agent_count == null ? undefined : r.real_agent_count) })
        .catch(() => { if (token.current === mine) setRealN(undefined) })
    }
  }
  const cancel = () => { token.current++; setAsk(false); setRealN(null) }
  const offBody = realN === null ? 'Checking your real data…'
    : realN === undefined ? 'The app goes back to your real database. Demo data stays in its own database.'
    : realN > 0 ? `You'll see your ${realN} real agent${realN === 1 ? '' : 's'}. The app goes back to your real database; demo data stays in its own database.`
    : 'No real agents yet: Fleet will be empty until you register or import agents. Demo data stays in its own database.'
  const flip = async () => {
    token.current++; setAsk(false); setRealN(null); setBusy(true); setErr('')
    try {
      onChange(await settingsApi.put('demo_mode', !on))
      refreshHealth()
    } catch (e) { setErr(problem(e)) } finally { setBusy(false) }
  }
  return (
    <section id="st-demo" className="ds-card st-card" aria-labelledby="st-demo-h" data-testid="settings-card-demo">
      <header className="st-card-head st-card-head-row">
        <h2 id="st-demo-h" className="ds-section-title">Demo mode</h2>
        <Badge tone={on ? 'warn' : 'none'}>{on ? 'On' : 'Off'}</Badge>
      </header>
      <div className="st-demo" data-testid="setting-demo_mode">
        <div className="st-ctl">
          <button type="button" role="switch" aria-checked={on} aria-labelledby="st-demo-h" aria-describedby="st-demo-help"
                  className="st-switch" disabled={entry.locked || busy} onClick={openAsk}><span className="st-knob" /></button>
          {entry.locked && <Badge tone="lock">Set by environment variable {entry.env}</Badge>}
          <span className="ds-sr" role="status">{busy ? (on ? 'Switching to your real data' : 'Switching to demo data. Seeding can take a few seconds') : ''}</span>
        </div>
        <div id="st-demo-help" className={err ? 'st-hint st-hint-error' : 'st-hint'} role={err ? 'alert' : undefined}>
          {err ? <>{Icon.err}<span>{err}</span> <Button onClick={openAsk}>Retry</Button></> : <>
            <span className="st-help st-help-strong">{on ? 'Demo mode is on. You are seeing simulated data.' : 'Demo mode is off. You are seeing your real data.'}</span>
            <span className="st-help">{entry.help} Turning it on switches to a separate demo database with simulated agents, runs and approver. Your real data is never changed.</span>
            {entry.locked && <span className="st-help st-env">Set by environment variable <code>{entry.env}</code>. Change it there.</span>}
          </>}
        </div>
      </div>
      {ask && (
        <ConfirmDialog
          title={on ? 'Turn demo mode off?' : 'Turn demo mode on?'}
          body={on ? offBody
            : 'The app switches to a separate demo database with simulated agents, runs and a simulated approver. Your real database is not touched.'}
          onConfirm={flip} onCancel={cancel} busy={on && realN === null} />
      )}
    </section>
  )
}

function Card({ id, title, subtitle, children, plain }) {
  const hid = `${id}-h`
  return (
    <section id={id} className="ds-card st-card" aria-labelledby={hid}>
      <header className="st-card-head">
        <h2 id={hid} className="ds-section-title">{title}</h2>
        {subtitle && <p className="st-sub">{subtitle}</p>}
      </header>
      <div className={plain ? 'st-plat-wrap' : 'st-rows'}>{children}</div>
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
        {p.fields.map(([n, span]) => <SettingField key={n} layout="stack" span={span} entry={by[n]} onChange={update} />)}
      </div>
    </section>
  )
}

function SectionNav({ ready }) {
  const [active, setActive] = React.useState('st-demo')
  React.useEffect(() => {
    if (!ready || !('IntersectionObserver' in window)) return undefined
    const ids = NAV.map((n) => n[0])
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
  return (
    <nav className="st-nav" aria-label="Settings sections">
      {NAV.map(([id, text]) => (
        <a key={id} href="#/settings" onClick={go(id)} className="st-nav-link" aria-current={active === id ? 'location' : undefined}>{text}</a>
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
  const head = (w) => <div className="st-card-head"><span className="ds-skel" style={{ width: w, height: 18 }} /><span className="ds-skel ds-skel-s" style={{ width: '55%' }} /></div>
  const plat = (k) => (
    <div className="ds-card st-plat st-sk-plat" key={k}>
      <span className="ds-skel" style={{ width: 150, height: 16, marginBottom: 14 }} />
      <div className="st-sk-pair"><span className="ds-skel st-sk-input" /><span className="ds-skel st-sk-input" /></div>
      <div className="st-sk-pair"><span className="ds-skel st-sk-input" /><span className="ds-skel st-sk-input" /></div>
    </div>
  )
  return (
    <div role="status" data-testid="settings-loading" className="st-layout st-loading" aria-busy="true">
      <span className="ds-sr">Loading settings</span>
      <div className="st-nav st-nav-sk" aria-hidden="true">{[0, 1, 2, 3].map((i) => <span key={i} className="ds-skel" style={{ width: '70%' }} />)}</div>
      <div className="st-main" aria-hidden="true">
        <div className="ds-card st-card">{head(120)}<div className="st-sk-row"><span className="ds-skel" style={{ width: 44, height: 24, borderRadius: 12 }} /></div></div>
        <div className="ds-card st-card">{head(140)}<div className="st-rows">{[0, 1, 2, 3, 4, 5, 6, 7, 8].map(row)}</div></div>
        <div className="ds-card st-card">{head(190)}<div className="st-plat-wrap"><div className="st-plat-grid">{[0, 1, 2, 3].map(plat)}</div></div></div>
        <div className="ds-card st-card">{head(80)}<div className="st-rows">{row(0)}</div></div>
      </div>
    </div>
  )
}

const Glyph = {
  lock: <svg width="28" height="28" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.2" /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" fill="none" stroke="currentColor" strokeWidth="1.2" /></svg>,
  user: <svg width="28" height="28" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="7" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M3 17c.8-3.3 3.6-5 7-5s6.2 1.7 7 5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>,
  err: <svg width="28" height="28" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M10 5.5v5.5M10 13.5v1.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>,
}

export default function Settings() {
  const [state, setState] = React.useState({ phase: 'loading' })
  const [me, setMe] = React.useState(null)  // GET /api/settings/access: { admin, user, via, owner, admins_configured }
  const load = React.useCallback(() => {
    setState({ phase: 'loading' })
    const minShow = new Promise((r) => setTimeout(r, 250))  // the skeleton never just flashes
    settingsApi.access().then(setMe, () => setMe(null))
    settingsApi.list().then(
      (data) => minShow.then(() => setState({ phase: 'ready', data })),
      (e) => {
        if (e.status === 401) setState({ phase: 'signin' })
        else if (e.status === 403) {
          settingsApi.access().then((a) => setState({ phase: 'denied', unconfigured: a && a.admins_configured === false, owner: a && a.owner }),
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
  } else if (state.phase === 'signin') {
    body = (
      <div className="ds-card ds-empty" data-testid="settings-signin">
        <span className="st-empty-icon" aria-hidden="true">{Glyph.user}</span>
        <h2 className="ds-empty-title">Sign in to view Settings</h2>
        <p className="ds-helper">Settings are locked until you sign in. Sign in to Domino, then reload this page. Settings can be changed by the project owner and listed admins.</p>
        <Button variant="primary" onClick={() => window.location.reload()}>Reload after signing in</Button>
      </div>
    )
  } else if (state.phase === 'denied') {
    body = (
      <div className="ds-card ds-empty" data-testid="settings-no-access">
        <span className="st-empty-icon" aria-hidden="true">{Glyph.lock}</span>
        <h2 className="ds-empty-title">Settings are read-only for you</h2>
        <p className="ds-helper">
          {state.unconfigured
            ? 'No owner or admins are configured, so nobody can open Settings yet. Set ACT_ADMINS to a list of usernames.'
            : `You can see that this page exists, but only the project owner and listed admins can view or edit settings. Ask ${state.owner ? state.owner : 'the project owner'} to add you under Access.`}
        </p>
      </div>
    )
  } else if (state.phase === 'error') {
    body = (
      <div className="ds-card ds-empty ds-empty-error" data-testid="settings-error" role="alert">
        <span className="st-empty-icon st-empty-danger" aria-hidden="true">{Glyph.err}</span>
        <h2 className="ds-empty-title">We could not load Settings</h2>
        <p className="ds-helper">Something went wrong on our side. Your settings are unchanged.</p>
        <Button variant="primary" onClick={load}>Try again</Button>
      </div>
    )
  } else {
    const by = Object.fromEntries(state.data.settings.map((s) => [s.name, s]))
    const owner = (me && me.owner) || ''
    body = (
      <>
        <div className="st-layout" data-testid="settings-body">
          <SectionNav ready />
          <div className="st-main">
            <DemoCard entry={by.demo_mode} onChange={update} />
            <Card id="st-integrations" title="Integrations" subtitle="An environment variable with the same name wins and is read-only here. Secrets are encrypted and write-only.">
              {INTEGRATIONS.map((n) => <SettingField key={n} entry={by[n]} onChange={update} />)}
            </Card>
            <Card id="st-credentials" plain title="Credentials"
                  subtitle="Used by the import wizard to read agents from each platform. Secrets are encrypted, write-only and never shown again.">
              {state.data.warnings.length > 0 && (
                <div className="st-warning" role="status" data-testid="secret-key-warning">
                  {Icon.warn}
                  <div>{state.data.warnings.map((w) => <p key={w}>{w.split(/(ACT_SECRET_KEY)/).map((t, i) => (t === 'ACT_SECRET_KEY' ? <code key={i}>{t}</code> : t))}</p>)}</div>
                </div>
              )}
              <div className="st-plat-grid">
                {PLATFORMS.map((p) => <PlatformCard key={p.id} p={p} by={by} update={update} />)}
              </div>
            </Card>
            <Card id="st-access" title="Access" subtitle="Only the project owner and these admins can open this page.">
              {owner && (
                <div className="st-owner-row">
                  <span className="st-chip" data-testid="admins-fixed-owner">{Icon.lock}<strong>{owner}</strong><span>Project owner · always admin</span></span>
                </div>
              )}
              <SettingField entry={by.admins} onChange={update} />
            </Card>
          </div>
        </div>
      </>
    )
  }
  const who = me && me.user ? (me.via === 'workspace' ? `Signed in as ${me.user} (workspace)` : `Signed in as ${me.user}`) : null
  return (
    <div className="ds-page ds-container" data-testid="settings-page">
      <PageHeader title="Settings" description="Connections, credentials and access for this Control Tower. Changes apply within seconds, with no restart." />
      {who && <p className="st-who" data-testid="settings-signed-in">{who}</p>}
      {body}
    </div>
  )
}
