import React from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { clean, fmtStatus } from '../format'
import { demoApi, health, settingsApi } from '../api'

export function TierBadge({ tier }) {
  if (tier == null) return <span className="badge tier-none" data-testid="tier-badge" data-tier="">Unrated</span>
  return <span className={`badge tier-${tier}`} data-testid="tier-badge" data-tier={tier}>Tier {tier}</span>
}

export function StatusPill({ status, testid }) {
  return <span className={`pill status-${status}`} data-testid={testid}>{fmtStatus(status)}</span>
}

export function Panel({ title, children, testid, aside, className }) {
  return (
    <section className={className ? `panel ${className}` : 'panel'} data-testid={testid}>
      <header className="panel-head"><h2>{title}</h2>{aside}</header>
      <div className="panel-body">{children}</div>
    </section>
  )
}

export function Loading({ what }) { return <p className="muted">Loading {what}...</p> }
export function ErrorBox({ error }) {
  return <div className="alert error" role="alert">{error?.status ? `${error.status}: ` : ''}{clean(error?.message || String(error))}</div>
}

export function useHealth() {
  const [h, setH] = React.useState(null)
  React.useEffect(() => {
    const load = () => health().then(setH)
    load()
    window.addEventListener('act:health', load)
    return () => window.removeEventListener('act:health', load)
  }, [])
  return h
}

function DemoBanner({ health: h, admin }) {
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState(null)
  const reset = async () => {
    setBusy(true); setErr(null)
    try { await demoApi.reset(); sessionStorage.setItem('act-demo-reset', '1'); window.location.reload() } catch (e) { setErr(e.message); setBusy(false) }
  }
  const [note, setNote] = React.useState(false)
  React.useEffect(() => {
    // StrictMode-safe: the flag is cleared by the timer, not by the first effect run.
    if (sessionStorage.getItem('act-demo-reset')) {
      setNote(true)
      const t = setTimeout(() => { sessionStorage.removeItem('act-demo-reset'); setNote(false) }, 5000)
      return () => clearTimeout(t)
    }
  }, [])
  const [open, setOpen] = React.useState(true)
  const n = h.stand_ins.length
  return (
    <div className="demo-banner" data-testid="demo-banner" role="region" aria-label="Demo mode">
      <div className="ds-shell-inner demo-inner">
        <button type="button" className="demo-toggle" data-open={open} aria-expanded={open} aria-controls={h.stand_ins.map((s) => `demo-chip-${s.name}`).join(" ")} onClick={() => setOpen((o) => !o)}><strong>Demo mode:</strong> simulated data and stand-ins ({n})</button>
        <span className="demo-sep" aria-hidden="true">·</span>
        <button type="button" className="demo-reset" onClick={reset} disabled={busy}>Reset demo</button>
        {admin && <>
          <span className="demo-sep" aria-hidden="true">·</span>
          <Link to="/settings" className="demo-settings">Demo settings</Link>
        </>}
        <span className="demo-break" hidden={!open} />
        {h.stand_ins.map((s) => <span className="demo-chip" id={`demo-chip-${s.name}`} key={s.name} data-stand-in={s.name} hidden={!open}>{s.label}</span>)}
        {h.demo_guard && <div className="alert error" role="alert" data-testid="demo-guard">{h.demo_guard}</div>}
        {err && <span className="alert error" role="alert">{err}</span>}
        {note && <div className="toast" role="status" data-testid="demo-reset-notice">Demo reset to its starting state</div>}
      </div>
    </div>
  )
}

// Fleet is the parent of agent and run pages: current page on "/", current section on its children.
function FleetLink() {
  const { pathname } = useLocation()
  const exact = pathname === '/'
  const child = /^\/(agents|runs)\//.test(pathname)
  return <Link to="/" data-testid="nav-fleet" aria-current={exact ? 'page' : child ? 'true' : undefined}>Fleet</Link>
}

// Connections is current on its pages and a section on the connect wizard (/connect/...).
function ConnectionsLink() {
  const { pathname } = useLocation()
  const exact = pathname === '/connections'
  const child = /^\/(connect|connections)\//.test(pathname)
  return <Link to="/connections" data-testid="nav-connections" aria-current={exact ? 'page' : child ? 'true' : undefined}>Connections</Link>
}

function GearIcon() {
  return (
    <svg data-testid="nav-settings-icon" className="ds-nav-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" fill="none" />
    </svg>
  )
}

function LogoMark() {
  return (
    <svg className="ds-logo" width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="currentColor" />
      <path d="M12 5v14M7 8.5h10M9 19h6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" fill="none" />
    </svg>
  )
}

export function Shell({ children }) {
  const h = useHealth()
  const [admin, setAdmin] = React.useState(false)
  const loc = useLocation()
  // Re-check on every route change: a hash-only navigation does not reload the page, and the caller may have changed.
  React.useEffect(() => { settingsApi.access().then((r) => setAdmin(!!r.admin), () => setAdmin(false)) }, [loc.pathname])
  return (
    <div className="app">
      <a href="#main" className="ds-skip" onClick={(e) => { e.preventDefault(); const m = document.getElementById('main'); if (m) m.focus() }}>Skip to content</a>
      {h && h.demo_mode && <DemoBanner health={h} admin={admin} />}
      <header className="ds-topbar" data-testid="app-topbar">
        <div className="ds-shell-inner ds-topbar-row">
          <Link to="/" className="ds-brand"><LogoMark />Agent Control Tower</Link>
          <span className="ds-subtitle" data-testid="app-subtitle">Agent system of record</span>
          <nav aria-label="Primary" className="ds-nav">
            <NavLink to="/home" data-testid="nav-home">Home</NavLink>
            <FleetLink />
            <NavLink to="/findings" data-testid="nav-findings">Findings</NavLink>
            <NavLink to="/policies" data-testid="nav-policies">Policies</NavLink>
            <ConnectionsLink />
            <NavLink to="/audit" data-testid="nav-audit">Audit</NavLink>
            <NavLink to="/settings"><GearIcon />Settings</NavLink>
          </nav>
        </div>
      </header>
      <main className="content" id="main" tabIndex={-1}>{children}</main>
    </div>
  )
}

export function useAsync(fn, deps) {
  const [state, setState] = React.useState({ loading: true, data: null, error: null })
  React.useEffect(() => {
    let live = true
    setState((s) => ({ loading: true, data: s.data, error: null }))
    fn().then((data) => live && setState({ loading: false, data, error: null }),
              (error) => live && setState((s) => ({ loading: false, data: s.data, error })))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}
