import React from 'react'
import { clean } from '../format'
import { StatusPill, TierBadge } from './ui'
import { Card } from './agentui'

const SIGNATURE = { signed: 'Signed (verified)', unsigned: 'Unsigned', invalid: 'Invalid signature' }

function isoDate(ts) {
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? clean(String(ts || '')) : d.toISOString().slice(0, 10)
}

export default function Passport({ agent }) {
  const p = agent.passport || {}
  const sig = SIGNATURE[agent.card_signature] || 'Unsigned'
  const sigKey = SIGNATURE[agent.card_signature] ? agent.card_signature : 'unsigned'
  return (
    <Card title="AI Passport" testid="passport" actions={<TierBadge tier={agent.tier} />}>
      <dl className="kv">
        <dt>Identity</dt><dd data-testid="passport-identity">{clean(p.identity || agent.name)}</dd>
        <dt>Allowed skills</dt>
        <dd><ul className="tags">{(p.allowed_skills || []).map((s, i) => <li key={`${i}-${s}`} className="chip" data-testid="passport-skill">{clean(s)}</li>)}
          {!(p.allowed_skills || []).length && <li className="muted">none</li>}</ul></dd>
        <dt>Data scopes</dt>
        <dd><ul className="tags">{(p.data_scopes || []).map((s, i) => <li key={`${i}-${s}`} className="chip" data-testid="passport-scope">{clean(s)}</li>)}
          {!(p.data_scopes || []).length && <li className="muted">none</li>}</ul></dd>
        <dt>Expires</dt><dd data-testid="passport-expiry">{isoDate(p.expires_at)}</dd>
        <dt>Card signature</dt><dd data-testid="passport-signature" data-signature={sigKey} data-severity={sigKey === 'invalid' ? 'warning' : undefined} className={`sig sig-${sigKey}${sigKey === 'invalid' ? ' warn' : ''}`}>{sig}</dd>
        <dt>Status</dt><dd><StatusPill status={agent.status} testid="passport-status" /></dd>
      </dl>
    </Card>
  )
}
