// Step 4 stub: scanning arrives with PO-6. Says so plainly and keeps the way back.
import React from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import WizardFrame from '../components/connect/WizardFrame'
import { PROVIDERS } from '../components/connect/providers'

export default function ConnectScan() {
  const { provider, connId } = useParams()
  const p = PROVIDERS[provider]
  const nav = useNavigate()
  if (!p) return <Navigate to="/connections" replace />
  return (
    <WizardFrame provider={provider} connId={connId} step={4} title={`Scan ${p.name}`}
                 description="ACT reads each agent's tools, identity, data access and owner tags. Nothing is imported until you review.">
      <div className="ds-card ds-empty" data-testid="scan-soon">
        <h2 className="ds-empty-title">Scanning arrives next</h2>
        <p className="ds-helper">Your access check and scope are saved on this connection. The scan, the review table and the import are the next release.</p>
        <div className="po-empty-actions">
          <button type="button" className="ds-btn" onClick={() => nav(`/connect/${provider}/${connId}/scope`)}>Back to scope</button>
          <button type="button" className="ds-btn ds-btn-primary" onClick={() => nav('/connections')}>Back to Connections</button>
        </div>
      </div>
    </WizardFrame>
  )
}
