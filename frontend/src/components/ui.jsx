import React from 'react'
import { Link } from 'react-router-dom'

export function TierBadge({ tier }) {
  if (tier == null) return <span className="badge tier-none" data-testid="tier-badge" data-tier="">Unrated</span>
  return <span className={`badge tier-${tier}`} data-testid="tier-badge" data-tier={tier}>Tier {tier}</span>
}

export function StatusPill({ status, testid }) {
  return <span className={`pill status-${status}`} data-testid={testid}>{status || 'unknown'}</span>
}

export function Panel({ title, children, testid, aside }) {
  return (
    <section className="panel" data-testid={testid}>
      <header className="panel-head"><h2>{title}</h2>{aside}</header>
      <div className="panel-body">{children}</div>
    </section>
  )
}

export function Loading({ what }) { return <p className="muted">Loading {what}...</p> }
export function ErrorBox({ error }) {
  return <div className="alert error" role="alert">{error?.status ? `${error.status}: ` : ''}{error?.message || String(error)}</div>
}

export function Shell({ children }) {
  return (
    <div className="app">
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
