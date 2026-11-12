// Step 2: Check access. Runs the checks as the page opens, shows a fix for each missing item, re-checks on demand
// (and every 30 s for 10 minutes while a required check fails, because cloud IAM takes time to propagate).
import React from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { connectionsApi } from '../api'
import { Alert } from '../components/form'
import WizardFrame from '../components/connect/WizardFrame'
import PermissionChecklist, { SkeletonChecks, summarize, withKeys } from '../components/connect/PermissionChecklist'
import { SampleBanner } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'

export default function ConnectAccess() {
  const { provider, connId } = useParams()
  const p = PROVIDERS[provider]
  const nav = useNavigate()
  const [items, setItems] = React.useState(null)
  const [busy, setBusy] = React.useState(true)
  const [error, setError] = React.useState(false)
  const [sample, setSample] = React.useState(false)
  const [announce, setAnnounce] = React.useState('')
  const runs = React.useRef(0)
  const [untrusted, setUntrusted] = React.useState(0) // polls spent waiting for ACT to see the trust (409 from /check)

  const run = React.useCallback(async () => {
    const mine = ++runs.current
    setBusy(true); setError(false)
    try {
      const r = await connectionsApi.check(connId)
      if (mine !== runs.current) return
      const rows = withKeys(r.permissions || [])
      setItems(rows)
      setAnnounce(summarize(rows).text)
      setUntrusted(0)
    } catch (e) {
      if (mine !== runs.current) return
      // 409: ACT hasn't seen the trust yet (a sample connection trusts itself a moment after it starts). Keep watching.
      if (e && e.status === 409) { setUntrusted((n) => n + 1); return }
      setError(true)
    }
    if (mine === runs.current) setBusy(false)
  }, [connId])

  React.useEffect(() => {
    connectionsApi.get(connId).then((c) => setSample(!!c.sample), () => {})
    setAnnounce(`Checking access.`)
    run()
    return () => { runs.current += 1 }
  }, [connId, run])

  // While the trust is not seen, ask the connection (that read also advances a sample) and check again.
  React.useEffect(() => {
    if (!untrusted) return undefined
    if (untrusted > 40) { setBusy(false); setError(true); return undefined }
    const t = setTimeout(async () => { try { await connectionsApi.get(connId) } catch { /* retry below */ } run() }, 1500)
    return () => clearTimeout(t)
  }, [untrusted, connId, run])

  const sum = items ? summarize(items) : null
  // Re-check automatically while a required check fails: every 30 s, for 10 minutes.
  const tries = React.useRef(0)
  React.useEffect(() => {
    if (!sum || !sum.reqMissing.length || busy || tries.current >= 20) return undefined
    const t = setTimeout(() => { tries.current += 1; run() }, 30000)
    return () => clearTimeout(t)
  }, [sum && sum.reqMissing.length, busy, items, run]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!p) return <Navigate to="/connections" replace />
  const canContinue = !!sum && !busy && sum.reqMissing.length === 0
  const lacking = sum && sum.optMissing.length ? sum.optMissing[0].name.replace(/^(Read|Signed in as)\s+/i, '').replace(/^the\s+/i, '') : ''
  const continueLabel = canContinue && sum.optMissing.length ? `Continue without ${lacking}` : 'Continue to scope'
  const reason = busy && !items ? 'Checks are running.' : sum && sum.reqMissing.length ? 'Required checks must pass before you continue.' : ''

  return (
    <WizardFrame provider={provider} connId={connId} step={2} title="Check access"
                 description={`ACT is confirming what the new ${provider === 'aws' ? 'role' : 'trust'} can read. Each check runs a single read-only call.`}>
      {sample && <SampleBanner provider={p.name} />}
      <section className="ds-card po-panel" aria-labelledby="ck-title" aria-busy={busy ? 'true' : undefined}>
        <div className="po-panel-head">
          <h2 id="ck-title">Permissions</h2>
          <span className="ds-helper po-flat" data-testid="permission-summary">{untrusted ? 'Waiting for the trust…' : busy && !items ? 'Checking…' : busy ? 'Checking again…' : error ? 'Checks did not run' : sum ? sum.text : ''}</span>
        </div>
        <div className="po-panel-body">
          {error && (
            <Alert title="ACT couldn't run the checks">This is on ACT's side, not {p.name}'s. Try again.</Alert>
          )}
          {!error && !items && <SkeletonChecks />}
          {items && !error && <PermissionChecklist items={items} />}
        </div>
      </section>
      <div className="po-footer">
        <div className="po-footer-l">
          <span className="ds-helper po-flat" id="ck-foot">
            {sum && sum.reqMissing.length ? 'Fix the required items above, then check again. ACT re-checks every 30 seconds.' : reason || (sum && sum.optMissing.length ? 'Only optional items are missing. You can continue now and add them later.' : '')}
          </span>
        </div>
        <div className="po-footer-r">
          <button type="button" className="ds-btn" data-testid="permission-recheck" onClick={run} disabled={busy}>Check again</button>
          <button type="button" className="ds-btn ds-btn-primary" data-testid="permission-continue" disabled={!canContinue}
                  aria-describedby="ck-foot" onClick={() => nav(`/connect/${provider}/${connId}/scope`)}>{continueLabel}</button>
        </div>
      </div>
      <div className="ds-sr" role="status" aria-live="polite">{announce}</div>
    </WizardFrame>
  )
}
