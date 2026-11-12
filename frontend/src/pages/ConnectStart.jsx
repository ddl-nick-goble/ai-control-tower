// Step 1: Connect. Creates a draft connection (it holds the single-use state nonce), shows the handshake, watches
// for trust by polling and moves to Check access on its own.
import React from 'react'
import { Navigate, useNavigate, useParams, useSearchParams, Link } from 'react-router-dom'
import { connectionsApi } from '../api'
import { Alert } from '../components/form'
import WizardFrame from '../components/connect/WizardFrame'
import HandshakePanel from '../components/connect/HandshakePanel'
import { ErrorPanel, IconInfo, SampleBanner } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'

const TRUSTED = ['trusted', 'verified', 'partial', 'connected', 'needs_attention']

export default function ConnectStart() {
  const { provider } = useParams()
  const p = PROVIDERS[provider]
  const nav = useNavigate()
  const [params] = useSearchParams()
  const sample = params.get('sample') === '1'
  const [conn, setConn] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [rev, setRev] = React.useState(0)
  const [state, setState] = React.useState('draft')
  const [since, setSince] = React.useState(null)
  const [held, setHeld] = React.useState(false)

  // Create the draft. Cancelled on unmount so a fast navigation never leaves a half-set-up page.
  React.useEffect(() => {
    if (!p) return undefined
    let live = true
    setConn(null); setError(false); setState('draft')
    connectionsApi.create(provider, sample).then(
      (c) => { if (live) { setConn(c); setState(c.state); setSince(Date.now()) } },
      () => { if (live) setError(true) })
    return () => { live = false }
  }, [provider, sample, rev, p])

  const go = React.useCallback(() => {
    if (conn) nav(`/connect/${provider}/${conn.id}/access`)
  }, [conn, nav, provider])

  // Poll: every 3 s while the tab is visible, every 15 s when hidden.
  React.useEffect(() => {
    if (!conn || TRUSTED.includes(state)) return undefined
    let live = true, timer
    const tick = async () => {
      try { const c = await connectionsApi.get(conn.id); if (live) setState(c.state) } catch { /* keep waiting */ }
      if (live) timer = setTimeout(tick, document.visibilityState === 'visible' ? 3000 : 15000)
    }
    timer = setTimeout(tick, 1200)
    return () => { live = false; clearTimeout(timer) }
  }, [conn, state])

  // Detected: say so, wait 1.5 s, then continue (unless the person is mid-paste and will choose "Use this").
  const detected = TRUSTED.includes(state)
  React.useEffect(() => {
    if (!detected || held) return undefined
    const t = setTimeout(go, 1500)
    return () => clearTimeout(t)
  }, [detected, held, go])

  const cancel = async (e) => {
    e.preventDefault()
    if (conn) { try { await connectionsApi.remove(conn.id) } catch { /* the draft just stays unused */ } }
    nav('/connections')
  }

  if (!p) return <Navigate to="/connections" replace />
  return (
    <WizardFrame provider={provider} step={1} title={p.title} description={p.desc}>
      {sample && <SampleBanner provider={p.name} />}
      {p.info && (
        <div className="ds-alert ds-alert-info" role="note">
          <IconInfo />
          <div><strong>{p.info[0]}</strong><p>{p.info[1]}</p></div>
        </div>
      )}
      {error && (
        <ErrorPanel testid="handshake-error" retryTestid="handshake-retry" title="ACT couldn't start the setup"
                    onRetry={() => setRev((r) => r + 1)} retryLabel="Try again">
          This is on ACT's side, not {p.name}'s. Nothing was created in your account. Try again in a moment.
        </ErrorPanel>
      )}
      {!error && !conn && (
        <div className="ds-card po-panel" aria-busy="true" data-testid="handshake-loading">
          <div className="po-panel-head"><span className="ds-skel po-skel-title" /></div>
          <div className="po-panel-body"><span className="ds-skel" /><span className="ds-skel" /><span className="ds-skel po-skel-short" /></div>
          <span className="ds-sr" role="status">Preparing your setup link…</span>
        </div>
      )}
      {state === 'failed' && (
        <Alert title="The setup didn't finish" data-testid="handshake-error">
          {p.name} didn't accept the trust. Start again to get a fresh link.
        </Alert>
      )}
      {conn && (
        <HandshakePanel provider={provider} conn={conn} onPasteHold={setHeld} onUse={go} since={since}
                        listener={detected ? 'detected' : sample ? 'waiting' : 'idle'} />
      )}
      <p className="ds-helper po-cancel"><Link to="/connections" data-testid="wizard-cancel" onClick={cancel}>Cancel setup</Link> · Nothing is created in ACT until you import agents.</p>
    </WizardFrame>
  )
}
