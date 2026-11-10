import React from 'react'
import { Link } from 'react-router-dom'
import { clean, fmtStatus } from '../format'
import { demoApi, health } from '../api'

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
  React.useEffect(() => { health().then(setH) }, [])
  return h
}

function DemoBanner({ health: h }) {
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
  return (
    <div className="demo-banner" data-testid="demo-banner" role="region" aria-label="Demo mode">
      <strong>Demo mode: simulated data and stand-ins</strong>
      <ul>{h.stand_ins.map((s) => <li key={s.name} data-stand-in={s.name}>{s.label}</li>)}</ul>
      <button className="btn" onClick={reset} disabled={busy}>Reset demo</button>
      {h.demo_guard && <div className="alert error" role="alert" data-testid="demo-guard">{h.demo_guard}</div>}
      {err && <span className="alert error" role="alert">{err}</span>}
      {note && <div className="toast" role="status" data-testid="demo-reset-notice">Demo reset to its starting state</div>}
    </div>
  )
}

export function Shell({ children }) {
  const h = useHealth()
  return (
    <div className="app">
      {h && h.demo_mode && <DemoBanner health={h} />}
      <nav className="topbar">
        <Link to="/" className="brand">Agent Control Tower</Link>
        <span className="muted">Agent system of record</span>
      </nav>
      <main className="content">{children}</main>
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
