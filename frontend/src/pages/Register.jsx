import React from 'react'
import { Link } from 'react-router-dom'
import { registerExternal } from '../api'
import { clean } from '../format'
import Passport from '../components/Passport'
import { Identity } from './Agent'

// No GET /api/tiering/criteria route exists, so the seven names from config/tiering.yaml are listed here.
const CRITERIA = [
  ['decision_impact', 'Decision impact', 'How material are the decisions this agent makes or drives?'],
  ['financial_exposure', 'Financial exposure', 'How much money can it move or commit?'],
  ['customer_impact', 'Customer impact', 'How strongly can it affect customers?'],
  ['autonomy', 'Autonomy', 'How much does it act without human review?'],
  ['data_sensitivity', 'Data sensitivity', 'How sensitive is the data it can reach?'],
  ['reversibility', 'Reversibility', 'How hard is it to undo what it does?'],
  ['regulatory_exposure', 'Regulatory exposure', 'How much regulatory or legal exposure does it carry?'],
]

function describeError(err) {
  const detail = clean(err.message || '')
  const lead = {
    422: 'The agent card could not be accepted.',
    502: 'The agent card could not be fetched from that address.',
    504: 'The agent card server took too long to respond.',
  }[err.status] || 'Registration failed.'
  const d = detail.replace(/^\s*\d{3}\s*[:\-]\s*/, '').replace(/\bcard_url\b/g, 'The card URL')
  return d && err.status !== 502 ? `${lead} ${d}` : lead
}

export default function Register() {
  const [url, setUrl] = React.useState('')
  const [vals, setVals] = React.useState(() => Object.fromEntries(CRITERIA.map(([k]) => [k, '3'])))
  const [scopes, setScopes] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState(null)
  const [agent, setAgent] = React.useState(null)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null); setAgent(null)
    const criteria = Object.fromEntries(CRITERIA.map(([k]) => [k, Number(vals[k])]))
    criteria.data_scopes = scopes.split(',').map((s) => s.trim()).filter(Boolean)
    try {
      setAgent(await registerExternal({ card_url: url.trim(), criteria }))
    } catch (err) {
      setError(describeError(err))
    } finally { setBusy(false) }
  }

  return (
    <div>
      <p><Link to="/">Fleet</Link> / Register external agent</p>
      <h1>Register external agent</h1>
      <form onSubmit={submit} className="panel">
        <div className="panel-body">
          <p><label>Agent card URL<br />
            <input type="url" data-testid="card-url" value={url} onChange={(e) => setUrl(e.target.value)}
                   className="text-input" placeholder="https://agent.example.com/.well-known/agent-card.json" />
          </label></p>
          <h2>Consequence criteria</h2>
          <p className="muted">Score each from 1 (low) to 5 (high).</p>
          {CRITERIA.map(([k, name, prompt]) => (
            <p key={k}><label>{name}: <span className="muted">{prompt}</span><br />
              <select data-testid={`criterion-${k}`} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label></p>
          ))}
          <p><label>Data scopes (comma-separated)<br />
            <input type="text" data-testid="data-scopes" value={scopes} onChange={(e) => setScopes(e.target.value)}
                   className="text-input" placeholder="accounts:read, payments:write" />
          </label></p>
          {error && <div className="alert error" role="alert" data-testid="register-error">{error}</div>}
          <button type="submit" className="btn primary" disabled={!url.trim() || busy}>Register</button>
        </div>
      </form>
      {agent && (
        <div>
          <Passport agent={agent} />
          <Identity agent={agent} />
          <p><Link to={`/agents/${encodeURIComponent(agent.id)}`}>Open the agent page</Link></p>
        </div>
      )}
    </div>
  )
}
