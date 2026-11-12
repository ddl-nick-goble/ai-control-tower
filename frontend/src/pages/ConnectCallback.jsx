// The return-link landing (#/connect/:provider/callback?state=...). Posts the link's values to the API, then moves
// to Check access. Any failure is plain language with a way back.
import React from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { connectionsApi } from '../api'
import { Alert } from '../components/form'
import { PROVIDERS } from '../components/connect/providers'

export default function ConnectCallback() {
  const { provider } = useParams()
  const { search } = useLocation()
  const nav = useNavigate()
  const p = PROVIDERS[provider]
  const [problem, setProblem] = React.useState(null)
  const h1 = React.useRef(null)
  React.useEffect(() => {
    let live = true
    connectionsApi.callback(search).then(
      (r) => {
        if (!live) return
        if (r.ok && r.connection_id) nav(`/connect/${r.provider || provider}/${r.connection_id}/access`, { replace: true })
        else setProblem(r.message || "ACT doesn't recognize that setup link. Go back to ACT, start the connection again and use the new link.")
      },
      () => { if (live) setProblem("ACT couldn't read that setup link just now. Go back to ACT, start the connection again and use the new link.") })
    return () => { live = false }
  }, [search, provider, nav])
  React.useEffect(() => { h1.current?.focus({ preventScroll: true }) }, [problem])
  return (
    <div className="ds-page ds-container po-page" data-testid="callback-page">
      <header className="ds-page-header">
        <h1 className="ds-title po-title" ref={h1} tabIndex={-1} data-testid="step-heading">
          {problem ? 'That setup link didn\'t work' : `Finishing the ${p ? p.name : 'provider'} setup`}
        </h1>
      </header>
      {problem ? (
        <div className="po-main po-narrow">
          <Alert title="Setup link not accepted">{problem}</Alert>
          <p><Link className="ds-btn ds-btn-primary" to={p ? `/connect/${provider}` : '/connections'}>{p ? `Start ${p.name} setup again` : 'Back to Connections'}</Link></p>
        </div>
      ) : (
        <div className="ds-card po-panel po-narrow" aria-busy="true">
          <div className="po-panel-body"><span className="ds-skel" /><span className="ds-skel po-skel-short" />
            <p className="ds-helper" role="status">Confirming the trust with {p ? p.name : 'your provider'}…</p></div>
        </div>
      )}
    </div>
  )
}
