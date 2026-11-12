// Small shared pieces for Connect a provider: icons, provider mark, status pills, sample badge, copy hook.
import React from 'react'
import { connectionsApi } from '../../api'

const svg = (children, size = 14, vb = 16) => (props) => (
  <svg width={size} height={size} viewBox={`0 0 ${vb} ${vb}`} aria-hidden="true" focusable="false" {...props}>{children}</svg>
)
const S = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round', strokeLinejoin: 'round' }
export const IconCheck = svg(<path d="M3 8.5l3 3 7-7" {...S} strokeWidth="2" />)
export const IconX = svg(<path d="M4 4l8 8M12 4l-8 8" {...S} strokeWidth="2" />, 12)
export const IconAlert = svg(<><path d="M8 1.5l7 12.5H1z" {...S} strokeWidth="1.5" /><path d="M8 6v3.5M8 11.5v.5" {...S} strokeWidth="1.6" /></>, 16)
export const IconInfo = svg(<><circle cx="8" cy="8" r="6.5" {...S} strokeWidth="1.5" /><path d="M8 7v4.5M8 4.6v.4" {...S} strokeWidth="1.6" /></>, 16)
export const IconDash = svg(<path d="M4 8h8" {...S} strokeWidth="2" />, 12)
export const IconExt = svg(<path d="M9 2h5v5M14 2L7 9M12 9.5V14H2V4h4.5" {...S} strokeWidth="1.5" />)
export const IconCopy = svg(<><rect x="5" y="5" width="9" height="9" rx="1.5" {...S} strokeWidth="1.5" /><path d="M3 11V3a1 1 0 011-1h7" {...S} strokeWidth="1.5" /></>)
export const IconChev = svg(<path d="M4 6l4 4 4-4" {...S} strokeWidth="1.8" />, 12)

export const IconClock = svg(<><circle cx="8" cy="8" r="6" {...S} strokeWidth="1.5" /><path d="M8 4.6V8l2.2 1.4" {...S} strokeWidth="1.5" /></>)
export const IconLock = svg(<><rect x="3" y="7" width="10" height="7" rx="1.5" {...S} strokeWidth="1.5" /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" {...S} strokeWidth="1.5" /></>)
export const IconUp = svg(<path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" {...S} strokeWidth="1.8" />, 12)

export function ProviderMark({ id, large }) {
  // Neutral placeholder marks (monograms). Vendor artwork is added after Domino legal sign-off (design spec section 10).
  const mono = { aws: 'AWS', google: 'GC', microsoft: 'MS', databricks: 'DB', snowflake: 'SF', domino: 'DOM', other: 'A2A' }[id] || '?'
  return <span className={`po-mark${large ? ' po-mark-lg' : ''}${id === 'domino' ? ' po-mark-domino' : ''}`} aria-hidden="true">{mono}</span>
}

export function Spinner() { return <span className="po-spin" aria-hidden="true" /> }

// Status pill: always carries text. kind: idle | ok | warn | err | busy (spinner) | info (accent, no spinner)
export function StatusPill({ kind, children, testid }) {
  const icon = kind === 'ok' ? <IconCheck /> : kind === 'warn' || kind === 'err' ? <IconAlert /> : kind === 'busy' ? <Spinner /> : null
  return <span className={`po-st po-st-${kind}`} data-testid={testid}>{icon}{children}</span>
}

export function SampleBadge() { return <span className="po-sample" data-testid="sample-badge">Sample data</span> }

export function SampleBanner({ provider, review }) {
  return (
    <div className="po-banner" role="note" data-testid="sample-banner">
      {review
        ? <span><strong>Sample data.</strong> These agents are fictional and were not read from {provider}. Imported records are marked Sample data in Fleet and are excluded from evidence packs.</span>
        : <span><strong>Sample data.</strong> This walks the setup on a fictional tenant. Nothing is read from {provider}, and nothing here touches a real account.</span>}
    </div>
  )
}

// **bold** markers in provider copy -> <strong>
export function Rich({ text }) {
  return text.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : <React.Fragment key={i}>{part}</React.Fragment>))
}

// Copy to the clipboard and say so in a polite live region the caller renders. `label` of the last copy shows for 1.8 s.
export function useCopy() {
  const [note, setNote] = React.useState('')
  const [last, setLast] = React.useState(null)
  const timer = React.useRef(null)
  const copy = React.useCallback(async (text, key) => {
    let ok = false
    try { await navigator.clipboard.writeText(text); ok = true } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0'
        document.body.appendChild(ta); ta.select(); ok = document.execCommand('copy'); document.body.removeChild(ta)
      } catch { ok = false }
    }
    setNote(ok ? 'Copied to clipboard' : 'Copy failed. Select the text and copy it.')
    setLast(ok ? key : null)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => { setLast(null); setNote('') }, 1800)
    return ok
  }, [])
  React.useEffect(() => () => clearTimeout(timer.current), [])
  return { copy, note, last }
}

export function useInterval(fn, ms) {
  const ref = React.useRef(fn)
  ref.current = fn
  React.useEffect(() => {
    if (ms == null) return undefined
    const t = setInterval(() => ref.current(), ms)
    return () => clearInterval(t)
  }, [ms])
}

export function ErrorPanel({ title, children, onRetry, retryLabel = 'Try again', testid, retryTestid }) {
  return (
    <div className="ds-card ds-empty ds-empty-error" data-testid={testid} role="alert">
      <svg className="ds-empty-icon" width="32" height="32" viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M10 5.5v5.5M10 13.5v1.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <h2 className="ds-empty-title">{title}</h2>
      <p className="ds-helper">{children}</p>
      {onRetry && <button type="button" className="ds-btn" data-testid={retryTestid} onClick={onRetry}>{retryLabel}</button>}
    </div>
  )
}

// One connection, loaded once; `reload` refetches. error is true when the request failed.
export function useConn(connId) {
  const [state, setState] = React.useState({ conn: null, error: false })
  const [rev, setRev] = React.useState(0)
  React.useEffect(() => {
    let live = true
    setState((s) => ({ conn: rev ? s.conn : null, error: false }))
    connectionsApi.get(connId).then((conn) => live && setState({ conn, error: false }), () => live && setState({ conn: null, error: true }))
    return () => { live = false }
  }, [connId, rev])
  return { ...state, reload: () => setRev((r) => r + 1) }
}
