import React from 'react'

// Small building blocks shared by the Agent and Run pages (cards, definition lists, copy, states).
// Built on the ds-* tokens in design.css; page-level classes are prefixed ag- / rt-.

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const prev = document.activeElement
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      if (prev && prev.focus) prev.focus({ preventScroll: true })
      return ok
    } catch { return false }
  }
}

export function CopyButton({ value, label, className = '' }) {
  const [copied, setCopied] = React.useState(null)
  const timer = React.useRef(null)
  React.useEffect(() => () => clearTimeout(timer.current), [])
  const onClick = async (e) => {
    e.stopPropagation()
    const ok = await writeClipboard(String(value))
    setCopied(ok ? 'ok' : 'fail')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(null), ok ? 1500 : 2500)
  }
  return (
    <>
      <button type="button" className={`ag-copy ${className}`} data-testid="copy-button" aria-label={`Copy ${label}`} title={`Copy ${label}`} onClick={onClick}>
        {copied === 'ok' ? (
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M10.5 3.5v-.2A1.3 1.3 0 0 0 9.2 2H3.8A1.3 1.3 0 0 0 2.5 3.3v5.4A1.3 1.3 0 0 0 3.8 10h.2" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
        )}
      </button>
      <span className={copied === 'fail' ? 'ag-copy-fail' : 'ds-sr'} role="status" aria-live="polite" aria-atomic="true">{copied === 'ok' ? 'Copied' : copied === 'fail' ? 'Copy failed' : ''}</span>
    </>
  )
}

// Long id / ARN: one line, ellipsis, full value in the title and a copy button.
export function IdValue({ value, label = 'value', testid = 'id-value', plain = false }) {
  const v = value == null || value === '' ? null : String(value)
  if (!v) return <span className="ag-none">—</span>
  return (
    <span className="ag-idv">
      <span className={`ds-trunc ag-idv-text${plain ? ' ag-idv-plain' : ''}`} data-testid={testid} title={v}>{v}</span>
      <CopyButton value={v} label={label} />
    </span>
  )
}

export function Card({ title, testid, id, className = '', actions, cardRef, children }) {
  return (
    <section className={`ds-card ag-card ${className}`} data-testid={testid} id={id} ref={cardRef} tabIndex={cardRef ? -1 : undefined}>
      <header className="ag-card-head">
        <h2 className="ag-card-title">{title}</h2>
        {actions}
      </header>
      <div className="ag-card-body">{children}</div>
    </section>
  )
}

export function Skel({ what, lines = 3, className = '' }) {
  const w = ['92%', '78%', '64%', '84%']
  return (
    <div className={`ag-skel ${className}`} role="status" aria-busy="true">
      <span className="ds-sr">Loading {what} ...</span>
      {Array.from({ length: lines }, (_, i) => <span key={i} className="ds-skel" style={{ width: w[i % w.length] }} />)}
    </div>
  )
}

export function InlineError({ error, onRetry, what }) {
  const msg = error && error.message ? error.message : String(error || 'Request failed')
  return (
    <div className="ds-alert ds-alert-error ag-inline-error" role="alert">
      <svg className="ds-alert-icon" width="16" height="16" viewBox="0 0 20 20" aria-hidden="true" style={{ marginTop: 2 }}>
        <path d="M10 2l9 16H1L10 2z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M10 8v4.5M10 15v.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <span className="ag-inline-error-text">{what ? `Could not load ${what}. ` : ''}{error && error.status ? `${error.status}: ` : ''}{msg}</span>
      {onRetry && <button type="button" className="ds-btn ds-btn-ghost ag-sm" onClick={onRetry}>Try again</button>}
    </div>
  )
}

export function Empty({ title, hint }) {
  return (
    <div className="ag-empty">
      <p className="ag-empty-title">{title}</p>
      {hint && <p className="ag-empty-hint">{hint}</p>}
    </div>
  )
}

export function Dl({ rows, testid, className = '' }) {
  return (
    <dl className={`ag-dl ${className}`} data-testid={testid}>
      {rows.filter(Boolean).map(([k, v, tid]) => (
        <div key={k}><dt>{k}</dt><dd data-testid={tid}>{v}</dd></div>
      ))}
    </dl>
  )
}

export const None = ({ children = 'none' }) => <span className="ag-none">{children}</span>

export function ChevronRight() {
  return <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

export function Arrow() {
  return <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9.5M9 4.5L12.5 8 9 11.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

export function ErrorPage({ title, message, onRetry, back }) {
  return (
    <div className="ds-page ds-container">
      <div className="ds-card ds-empty ds-empty-error" role="alert">
        <svg className="ds-empty-icon" width="32" height="32" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 2l9 16H1L10 2z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><path d="M10 8v4.5M10 15v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <h2 className="ds-empty-title">{title}</h2>
        <p className="ds-helper">{message}</p>
        <div className="ag-row">
          <button type="button" className="ds-btn" onClick={onRetry}>Try again</button>
          {back}
        </div>
      </div>
    </div>
  )
}
